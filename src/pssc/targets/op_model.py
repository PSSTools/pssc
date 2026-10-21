"""The elaborated operation model: one walk, shared by every backend.

`progseq_model` answers questions about a component ("is this field a register
group?", "what is this instance's offset?"). This is the layer above it: the
whole of what an operation-model backend needs from a model, gathered once, in
one order, so three backends cannot compute it three ways.

They did. Before this, each of `progseq_gen.py`, `c/c_progseq_gen.py` and
`cpp/cpp_progseq_gen.py` carried its own `_resolver`, its own `_count`, its own
regular-component walk and its own value-struct de-duplication. The copies had
drifted in a way nothing could see: **the SV and C walks disagreed on the order
of SIBLING components.** SV emitted them in declaration order, C in reverse
(its "post-order" was the reverse of a pre-order, which is only the same thing
when no component has two children). Both outputs were correct -- a child
preceded its parent either way -- so nothing failed, and the disagreement would
have surfaced as an unexplained diff the first time anyone compared two
backends' output for one model.

`components` is a true post-order and is the single answer. See
:func:`_post_order`.

Nothing here reimplements anything: it calls `progseq_model` and gathers the
results. A backend that needs something not on `OpModel` should reach for
`progseq_model` directly rather than recompute a walk.
"""
from __future__ import annotations

import abc
import argparse
import dataclasses as dc
import functools
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import (Any, Dict, FrozenSet, List, Mapping, Optional, Sequence,
                    Tuple)

from . import progseq_model as pm
from .base import Target
from .progseq_model import CompKind, CompNode


def resolver(ctx):
    """``resolve(dtype) -> defining component datatype``, following `DataTypeRef`.

    One copy. Each backend had its own, identical, and a fourth backend would
    have written a fifth.
    """
    tm = getattr(ctx, "type_map", {}) or {}

    def resolve(dtype):
        ref = getattr(dtype, "ref_name", None)
        if ref and ref in tm:
            return tm[ref]
        return dtype

    return resolve


def _install_copy(src: Path, dst: Path) -> None:
    """Put a copy of *src* at *dst*, whatever is at *dst* already.

    REPLACED, never written through: a copy an older pssc left read-only (it
    used ``copy2``, which kept the shipped file's mode) cannot be opened for
    writing, but the directory entry can be swapped. The new copy is the
    owner's to write, so the next compile can replace it too. The source is
    only read.
    """
    fd, tmp = tempfile.mkstemp(prefix=f".{dst.name}.", dir=str(dst.parent))
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.chmod(tmp, 0o644)
        try:
            # Windows will not replace a read-only file; POSIX does not care.
            os.chmod(dst, stat.S_IMODE(os.stat(dst).st_mode) | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_error(e: OSError):
    """An output that could not be written, as the user's problem to fix
    (a directory or a file they cannot write), not a compiler crash."""
    from ..driver import CompileError
    what = f"{e.filename}: {e.strerror}" if e.filename else str(e)
    return CompileError(f"cannot write the generated files ({what})")


def _post_order(tree: CompNode) -> List[CompNode]:
    """Regular components, CHILDREN BEFORE PARENTS, siblings in declaration order.

    A true post-order, and the distinction matters. `reversed(pre_order)` also
    puts children before parents and is what the C backend used, but it emits
    siblings backwards: for a root with children `al` then `be`, a true
    post-order gives `[al, be, top]` and the reversal gives `[be, al, top]`.
    Both compile. Only one of them matches the order the model was written in,
    and only one of them can be the answer if three backends are to agree.

    Two things need children-first and neither is cosmetic: a parent embeds its
    children by value, so the child's type must be complete where the parent is
    declared; and a parent's initialisation calls its children's, which in a
    single-header mode means the child's definition has to come first.

    De-duplicated by component TYPE (`walk_tree` already returns one node per
    datatype), because two instances of one component type share one emitted
    class, one prefix and one copy of every operation.
    """
    out: List[CompNode] = []
    seen = set()

    def visit(node: CompNode) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        for child in node.children:
            visit(child)
        if node.kind == CompKind.REGULAR:
            out.append(node)

    visit(tree)
    return out


def _pre_order(tree: CompNode) -> List[CompNode]:
    """Regular components, PARENTS BEFORE CHILDREN, siblings in declaration order.

    The root is first, which two passes depend on: symbol-prefix assignment
    gives index 0 the caller's `--prefix`, and the API-type collection walks the
    root's surface first so a type is emitted near the component that
    introduces it.
    """
    out: List[CompNode] = []
    seen = set()

    def visit(node: CompNode) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if node.kind == CompKind.REGULAR:
            out.append(node)
        for child in node.children:
            visit(child)

    visit(tree)
    return out


def _count(node: CompNode, kind: CompKind) -> int:
    """How many components of ``kind`` the tree holds. For logging."""
    seen = set()

    def visit(n) -> int:
        if id(n) in seen:
            return 0
        seen.add(id(n))
        return (1 if n.kind == kind else 0) + sum(visit(c) for c in n.children)

    return visit(node)


@dc.dataclass(frozen=True)
class OpModel:
    """Everything an operation-model backend needs from one elaborated model.

    Frozen, and the collections are tuples, because this is handed to emitters
    that must not be able to change what a later emitter sees. A backend that
    needs a derived list builds its own.
    """

    #: The translated context, for the few things that live on it (`type_map`).
    ctx: Any
    #: The root component datatype (`--root`, resolved).
    root: Any
    #: The walked tree, rooted at `root`.
    tree: CompNode

    #: Regular components, children before parents, siblings in declaration
    #: order. THE order for emission -- see `_post_order`.
    components: Tuple[CompNode, ...]
    #: The same set, root first. For prefix assignment and API-type collection.
    components_root_first: Tuple[CompNode, ...]

    #: Every register group under the tree, de-duplicated, nested groups first.
    reg_groups: Tuple[Any, ...]
    #: Register VALUE structs, de-duplicated in first-use order. The register
    #: model emits these, so an API-type pass must skip them or a model gets two
    #: incompatible declarations of one type.
    value_structs: Tuple[Any, ...]

    #: Declared `import target/solve function`s, by PSS name. Package scope only
    #: -- a component-scope import does not reach the IR today.
    imports: Mapping[str, Any]

    #: Which `solve function` names mean "constructor" for THIS run.
    #:
    #: Carried on the model rather than read from ambient state at each call
    #: site: `--ctor-name` used to be a process-global whose value outlived the
    #: compile that set it, and a compile's own answer belongs to the compile.
    #: `progseq_model.current_ctor_names()` remains the fallback for a caller
    #: with no model to hand.
    ctor_names: FrozenSet[str]

    #: Where the backend writes.
    out_dir: Path

    #: Exported actions (`--export-action`), each an entry point rendered as a
    #: task of the component it runs in. See `export_action.py`.
    entries: Tuple[Any, ...] = ()

    #: Package-scope functions the model calls, transitively, in first-reached
    #: order -- empty unless the target lowers them. See `pkg_functions.py`.
    functions: Tuple[Any, ...] = ()

    #: The component TYPES a backend that renders inheritance natively emits a
    #: class for: every type in the tree, plus the user base types they derive
    #: from, each base before anything derived from it. Equal to
    #: `comp_dtypes` for a target that flattens (`comp_inherit`).
    classes: Tuple[Any, ...] = ()

    #: The root component's exported functions (`export target function f;`
    #: in its body, an extension -- see `export_function.py`), in declaration
    #: order. Each is a function of the completed root.
    exports: Tuple[Any, ...] = ()

    @property
    def base_classes(self) -> List[Any]:
        """`classes` that are not in the tree: bases nothing instantiates."""
        have = {id(n.dtype) for n in self.components}
        return [c for c in self.classes if id(c) not in have]

    # -- convenience -------------------------------------------------------
    #
    # Delegations, not logic. They exist so an emitter says what it means
    # (`model.operations(comp)`) instead of restating a filter.

    @property
    def comp_dtypes(self) -> List[Any]:
        """`components`' datatypes, children first."""
        return [n.dtype for n in self.components]

    @property
    def comp_dtypes_root_first(self) -> List[Any]:
        return [n.dtype for n in self.components_root_first]

    def is_root(self, comp) -> bool:
        return id(getattr(comp, "dtype", comp)) == id(self.root)

    def operations(self, comp) -> List[Any]:
        """The component's exported operations, in declaration order."""
        dtype = getattr(comp, "dtype", comp)
        return [fn for fn in (getattr(dtype, "functions", None) or [])
                if pm.func_kind(fn, self.ctor_names) is pm.FuncKind.EXPORT_OP]

    def solve_functions(self, comp) -> List[Any]:
        """The component's solve functions other than its constructor, in
        declaration order: private operations, called in solve context."""
        dtype = getattr(comp, "dtype", comp)
        return [fn for fn in (getattr(dtype, "functions", None) or [])
                if pm.func_kind(fn, self.ctor_names)
                is pm.FuncKind.EXPORT_SOLVE]

    def entries_of(self, comp) -> List[Any]:
        """The entry points that run in ``comp``, in `--export-action` order."""
        dtype = getattr(comp, "dtype", comp)
        return [e for e in self.entries if e.comp is dtype]

    @functools.cached_property
    def bindings(self) -> Dict[int, Any]:
        """``{id(comp): group_binding.Binding}`` for every component type
        the backends render (`comp_dtypes` and `classes`), computed once over
        the WHOLE model: an enclosing component's `sub.g.set_handle(...)`
        counts. The gate and every backend read this one table, so they cannot
        disagree about which group is bound implicitly."""
        from . import group_binding
        comps = list({id(c): c for c in list(self.comp_dtypes)
                      + list(self.classes)}.values())
        tm = getattr(self.ctx, "type_map", {}) or {}
        return group_binding.bindings(comps, self.ctor_names, tm)

    def binding(self, comp):
        """*comp*'s ``Binding`` (see `bindings`)."""
        dtype = getattr(comp, "dtype", comp)
        b = self.bindings.get(id(dtype))
        if b is None:
            from . import group_binding
            tm = getattr(self.ctx, "type_map", {}) or {}
            b = group_binding.bindings([dtype], self.ctor_names, tm)[id(dtype)]
        return b

    def handle_params(self) -> Dict[Tuple[str, ...], Optional[str]]:
        """Each register group under the root, by instance path, mapped to
        the root constructor's parameter that gives its handle
        (`group_binding.handle_params`)."""
        from . import group_binding
        return group_binding.handle_params(self)

    def owner_of(self, fn):
        """The component type in the tree that declares ``fn``, or None."""
        for c in self.comp_dtypes:
            if any(f is fn for f in getattr(c, "functions", None) or []):
                return c
        return None

    def state_uses(self, fn):
        """What ``fn`` touches, transitively (`state_uses.StateUses`):
        register groups and registers, component fields, imports, unbounded
        loops. Memoised per function. Provisional (see
        `extension-stability.md`)."""
        cache = self.__dict__.setdefault("_state_uses_cache", {})
        hit = cache.get(id(fn))
        if hit is not None:
            return hit
        from . import executors as xtr
        from .state_uses import state_uses
        su = state_uses(fn, self.owner_of(fn), self.ctx)
        if xtr.has_executors(self) and (su.regs_read or su.regs_written
                                         or su.mem_accessed):
            su = dc.replace(su, executor_delegated=True)
        cache[id(fn)] = su
        return su

    def unpacked_signature(self, fn) -> List[Tuple[Tuple[str, ...], str]]:
        """``[(group_path, root_param)]`` for every register group ``fn``
        uses, transitively, ordered by the root constructor's parameter list:
        what a stateless API takes in place of the component handle.
        ``group_path`` is the instance path from the root.

        Raises a located ``diag.Refusal`` for a group whose handle is not a
        root constructor parameter passed down unchanged (`handle_params`), and
        for a function of a component type that appears more than once in the
        tree (its groups have no single path from the root)."""
        from ..diag import Refusal
        owner = self.owner_of(fn)
        prefix = self._instance_path(owner)
        if prefix is None:
            raise Refusal(
                f"'{fn.name}': its component appears more than once in the "
                f"tree, so its register groups have no single path from the "
                f"root", fn)
        hp = self.handle_params()
        rctor = self.ctor(self.root)
        order = {a.arg: i for i, a in enumerate(
            getattr(getattr(rctor, "args", None), "args", None) or [])}
        out = []
        for g in sorted(self.state_uses(fn).groups):
            path = prefix + g
            p = hp.get(path)
            if p is None:
                raise Refusal(
                    f"'{fn.name}' uses register group '{'.'.join(path)}', "
                    f"whose handle is not a parameter of the root "
                    f"constructor passed down unchanged", fn)
            out.append((path, p))
        out.sort(key=lambda e: (order.get(e[1], len(order)), e[0]))
        return out

    def _instance_path(self, comp) -> Optional[Tuple[str, ...]]:
        """The one instance path of ``comp`` from the root (``()`` for the
        root), or None if it has none or several."""
        from .group_binding import _resolve
        tm = getattr(self.ctx, "type_map", {}) or {}
        by_name = {getattr(c, "name", None): c for c in self.comp_dtypes}
        found: List[Tuple[str, ...]] = []

        def walk(dt, path, depth):
            if dt is comp:
                found.append(path)
            if depth > 64:
                return
            for sub in pm.sub_components(dt):
                walk(_resolve(sub.dtype, tm, by_name), path + (sub.name,),
                     depth + 1)

        walk(self.root, (), 0)
        return found[0] if len(found) == 1 else None

    def ctor(self, comp):
        """The component's constructor, or ``None``."""
        dtype = getattr(comp, "dtype", comp)
        for fn in (getattr(dtype, "functions", None) or []):
            if pm.func_kind(fn, self.ctor_names) is pm.FuncKind.CONSTRUCTOR:
                return fn
        return None

    def init_blocks(self, comp, kind: str) -> List[Any]:
        """The component's `exec <kind>` blocks (``init_down``/``init_up``),
        in source order -- which is the order they run in, as one block
        (LRM 20.1 d)."""
        from .comp_inherit import SUPER_BLOCK
        dtype = getattr(comp, "dtype", comp)
        return [fn for fn in (getattr(dtype, "functions", None) or [])
                if pm.exec_kind(fn) == kind
                and SUPER_BLOCK not in (fn.metadata or {})]

    def super_blocks(self, comp) -> List[Any]:
        """Private copies of a base component's exec blocks that `super;` in
        this component's blocks runs (`comp_inherit`). Not run otherwise."""
        from .comp_inherit import SUPER_BLOCK
        dtype = getattr(comp, "dtype", comp)
        return [fn for fn in (getattr(dtype, "functions", None) or [])
                if SUPER_BLOCK in (getattr(fn, "metadata", None) or {})]

    def initializes(self, comp) -> bool:
        """Does anything under ``comp``, itself included, declare an
        `exec init_down`/`init_up`? A subtree that does not needs no
        initialization pass."""
        dtype = getattr(comp, "dtype", comp)
        return (any(self.init_blocks(dtype, k) for k in pm.INIT_EXEC_KINDS)
                or any(self.initializes(s.dtype)
                       for s in pm.sub_components(dtype)))

    def func_kind(self, fn) -> pm.FuncKind:
        """Classify a function using THIS run's constructor names."""
        return pm.func_kind(fn, self.ctor_names)

    def is_ctor_name(self, name: str) -> bool:
        """Is ``name`` the constructor, at a call site where only the name is
        known? (`ch[i].initialize(...)` -- the callee's node is not to hand.)"""
        return name in self.ctor_names

    def channels(self, comp) -> List[Any]:
        return pm.channel_fields(getattr(comp, "dtype", comp))

    def sub_components(self, comp) -> List[Any]:
        return pm.sub_components(getattr(comp, "dtype", comp))

    def offset_of(self, group, instance: str) -> int:
        """Folded byte offset of ``instance`` within ``group``."""
        return pm.scalar_offset(group, instance)

    def base_stride_of(self, group, instance: str) -> Tuple[int, int]:
        """Folded ``(base, stride)`` of an instance ARRAY within ``group``."""
        return pm.array_base_stride(group, instance)

    def total_operations(self) -> int:
        return (sum(len(self.operations(n)) for n in self.components)
                + len(self.entries))


def _classes(ctx, components, native: bool) -> Tuple[Any, ...]:
    """`OpModel.classes`: the tree's types, with their user bases first."""
    from .comp_inherit import declared
    out: List[Any] = []
    seen: set = set()

    def add(d):
        if id(d) in seen:
            return
        dec = declared(ctx, d) if native else None
        if dec is not None:
            add(dec.base)
        seen.add(id(d))
        out.append(d)

    for n in components:
        add(n.dtype)
    return tuple(out)


def elaborate(ctx, root, out_dir, *, ctor_names: Optional[FrozenSet[str]] = None,
              entries: Sequence[Any] = (),
              pkg_functions: bool = False,
              native_inheritance: bool = False) -> OpModel:
    """Walk ``root``'s subtree and gather everything a backend needs.

    Called once per compile, before any file is opened, so a failure here
    cannot leave a partly-written artifact behind.
    """
    names = (frozenset(ctor_names) if ctor_names is not None
             else pm.current_ctor_names())
    from .comp_inherit import complete, problems
    complete(ctx)
    pm.flatten_struct_bases(ctx)
    from .array_methods import lower as lower_array_methods
    lower_array_methods(ctx)
    from .match_width import lower as widen_match_subjects
    widen_match_subjects(ctx)
    from .match_overlap import lower as guard_match_overlap
    guard_match_overlap(ctx)
    from .comp_foreach import lower as lower_comp_foreach
    lower_comp_foreach(ctx)
    from .index_bounds import lower as mark_index_bounds
    mark_index_bounds(ctx)
    tree = pm.walk_tree(root, resolver(ctx))
    post = _post_order(tree)
    pre = _pre_order(tree)
    bad = problems(ctx, pre)
    if bad:
        raise ValueError("component inheritance: " + "; ".join(bad))

    groups: List[Any] = []
    seen = set()
    for node in pre:
        for g in pm.collect_reg_groups(node.dtype):
            if id(g) not in seen:
                seen.add(id(g))
                groups.append(g)

    entries = _entries_in_tree(tuple(entries), post)
    from .export_function import root_exports
    exports = root_exports(ctx, root, post, names)
    classes = _classes(ctx, post, native_inheritance)
    have = {id(n.dtype) for n in post}
    extra = [c for c in classes if id(c) not in have]
    functions: Tuple[Any, ...] = ()
    if pkg_functions:
        from . import pkg_functions as pf
        from .validate_calls import package_bodies
        functions = pf.unique(package_bodies(
            root, ctx, names,
            [(e.comp, fn) for e in entries for fn in e.functions], extra))

    return OpModel(
        ctx=ctx,
        root=root,
        tree=tree,
        components=tuple(post),
        components_root_first=tuple(pre),
        reg_groups=tuple(groups),
        value_structs=tuple(pm.collect_value_structs(groups)),
        imports=dict(_import_map(ctx)),
        ctor_names=names,
        out_dir=Path(str(out_dir)),
        entries=entries,
        functions=functions,
        classes=classes,
        exports=exports,
    )


def _entries_in_tree(entries, components) -> Tuple[Any, ...]:
    """``entries``, each checked to run in a component of the walked tree."""
    from .export_action import ExportActionError
    have = {id(n.dtype) for n in components}
    for e in entries:
        if id(e.comp) not in have:
            raise ExportActionError(
                f"--export-action '{e.action}' runs in component "
                f"'{getattr(e.comp, 'name', '?')}', which is not in the tree "
                f"under --root")
    return entries


def _import_map(ctx) -> Dict[str, Any]:
    """Declared `import target/solve function`s, by PSS name.

    Package-scope only, which is where the front end surfaces them. A
    component-scope `import function` parses and then does not reach the IR at
    all, so it cannot be honoured here; the backends' diagnostics say so.
    """
    return {f.name: f for f in getattr(ctx, "import_functions", None) or []}


def count(tree: CompNode, kind: CompKind) -> int:
    """Public alias for the log line every backend prints."""
    return _count(tree, kind)


# --- the target base class --------------------------------------------------

def _drop_constraint_functions(ctx) -> None:
    """Remove each type's constraint functions (`_is_constraint`: a domain's
    `in`, 7.2.1 d) -- an op-model never solves, and every reader of a
    component's functions took one for an operation with no signature (E24).
    A domain's DEFAULT is the field's initial value, set by ast2ir."""
    seen = set()
    for dt in list((getattr(ctx, "type_map", None) or {}).values()):
        fns = getattr(dt, "functions", None)
        if id(dt) in seen or not fns:
            continue
        seen.add(id(dt))
        keep = [f for f in fns if not (getattr(f, "metadata", None) or {})
                .get("_is_constraint")]
        if len(keep) != len(fns):
            fns[:] = keep


class OpModelTarget(Target):
    """A target that projects a PSS component tree into a programming API.

    `Target` is the contract for any backend. This is the contract for the
    operation-model FAMILY, and it is where everything the three built-ins were
    doing identically now lives: resolving `--root`, honouring `--ctor-name`,
    elaborating the model, and gating illegal calls before a file is opened.

    A subclass supplies a name, a `legality_target`, whatever options are its
    own, and `emit`. Everything else is inherited -- including, deliberately,
    the checks: a backend cannot forget to run the legality gate, because it is
    not the backend that runs it. That is the fix for what P1.T1 found, where
    the gate had been written for SV and never propagated to the two backends
    with the narrowest lowering.

        class MyTarget(OpModelTarget):
            name = "op-model-mine"
            legality_target = "op-model-c"      # reuse a built-in's Tier 2
            def emit(self, model, opts):
                path = model.out_dir / "out.txt"
                path.write_text(...)
                return [path]
    """

    #: Which `call_legality` entry set applies. Defaults to :attr:`name`, which
    #: is right for a backend that declares its own; a derived style points at
    #: the built-in whose lowering it reuses.
    legality_target: str = ""

    #: Named in the "N call(s) cannot be lowered to X" diagnostic.
    language: str = ""

    #: Whether this backend renders component inheritance NATIVELY -- a class
    #: per component type, derived from its base's, with `super` as the
    #: language's own -- rather than from `comp_inherit`'s flattened view. Such
    #: a backend also emits base types nothing instantiates
    #: (`OpModel.classes`), and the gate checks their bodies too.
    native_inheritance: bool = False

    #: Whether this backend renders `OpModel.entries` (`--export-action`). A
    #: backend that does not refuses a model with entries rather than
    #: generating an API without them.
    supports_entries: bool = False

    #: Whether this backend lowers package-scope functions the model calls
    #: (`OpModel.functions`). A backend that does not refuses each call by
    #: name at the legality gate.
    supports_package_functions: bool = False

    #: Whether this backend runs the components' `exec init_down`/`init_up`
    #: blocks as part of building the tree. A backend that does not refuses a
    #: model that has them (`validate_calls.exec_blocks`).
    supports_init_blocks: bool = False

    #: Whether this backend passes an array parameter, and returns an array
    #: result, as PSS does: by handle (LRM 20.3.2). A backend that does not
    #: refuses a function with one (`validate_calls.array_signatures`); C++'s
    #: `std::array` would compile, by value, and lose the callee's writes.
    supports_array_params: bool = False

    #: Whether this backend renders the string methods that return a scalar
    #: or a string (LRM 7.6.3, `string_methods.RESULTS`) and the sub-string
    #: operator. One that does not refuses a call to one by name; a backend
    #: with no strings at all refuses the type first (`unsupported_types`).
    supports_string_methods: bool = False

    #: Types this backend does not render, by IR class name, with the reason
    #: a refusal gives (`unsupported_types`). Collections are refused on
    #: every op-model target for now (ruling R4, 2026-10-16); a subclass
    #: adds what it lacks (C and C++: `string`, R3).
    unsupported_types: Dict[str, str] = {
        "DataTypeList": "collections are not supported on op-model targets "
                        "yet; ruling R4",
        "DataTypeMap": "collections are not supported on op-model targets "
                       "yet; ruling R4",
        "DataTypeSet": "collections are not supported on op-model targets "
                       "yet; ruling R4",
    }

    #: Whether this backend delegates memory primitives -- and the register
    #: accesses built on them -- to the assigned executor's overrides (LRM
    #: 21.13.9.5), and renders `set_executor`. A backend that does not refuses
    #: a model whose executor overrides one (`executors.check`).
    supports_executor_delegation: bool = False

    # -- derivation ---------------------------------------------------------

    #: The target this one derives from, BY NAME -- "everything op-model-c
    #: does, under a different name, plus my changes". Three things follow the
    #: ancestor, and each is wired separately below because they are three
    #: mechanisms and a single integration test would let two of them quietly
    #: not work (design I15):
    #:
    #:   * **call legality**, snapshotted at construction (`inherit_legality`)
    #:   * **CLI options**, delegated in `add_args`
    #:   * **`target_cfg`**, merged in `resolved_target_cfg`
    #:
    #: A NAME rather than a class, because the ancestor may live in another
    #: distribution and deriving must not require importing it.
    derives_from: str = ""

    #: Tier-2 legality entries this target adds ON TOP of the ancestor's.
    #: Declared here rather than registered at import so that the registration
    #: happens once, at construction, in step with the target's own lifecycle.
    legality_entries: Sequence[Any] = ()

    def __init__(self) -> None:
        if self.derives_from:
            self.inherit_legality()

    def inherit_legality(self) -> None:
        """Register this target's legality as the ancestor's plus its own.

        A SNAPSHOT, taken now -- `register_extension(inherit=)`'s own rule. The
        ancestor gaining a Tier-2 entry later must not silently make a call
        legal in a derived backend that never learned to render it.
        """
        from .call_legality import register_extension
        register_extension(self.name, self.legality_entries,
                           inherit=self.derives_from, replace=True)

    def ancestor(self) -> Optional["OpModelTarget"]:
        """The target named by :attr:`derives_from`, or ``None``.

        Resolved late, from the registry: a plugin may derive from another
        plugin, and entry-point load order is not something either of them
        controls.
        """
        if not self.derives_from:
            return None
        from . import get as _get
        try:
            return _get(self.derives_from)
        except KeyError as e:
            raise ValueError(
                f"target '{self.name}' derives from '{self.derives_from}', "
                f"which is not registered: {e}") from None

    def resolved_target_cfg(self) -> Optional[Dict[str, bool]]:
        """The capabilities published to the model -- the ancestor's, with this
        target's own on top.

        Own wins per FLAG, not wholesale: a derived target that adds a
        scheduler says `{"HAVE_EVENT_WAIT": True}` and keeps everything else
        its ancestor established. Replacing the whole mapping would make that
        one-line statement silently drop the other flags, and a model reading
        the version marker beside them trusts all of them.
        """
        anc = self.ancestor()
        base = anc.resolved_target_cfg() if anc is not None else None
        if base is None:
            return super().resolved_target_cfg()
        merged = dict(base)
        merged.update(self.target_cfg or {})
        return merged

    def style_targets(self) -> List[str]:
        """Which style registries answer this target's `--style`, nearest first.

        The fourth thing derivation carries, and the one that is not optional:
        the `pssc.styles` group is keyed `"<target>:<name>"`, so without this a
        derived target has no styles at all -- not even `default`, which is
        pssc's own and which every C generation resolves. Its own name comes
        first, so a derived target can register a policy of its own under a
        name the ancestor also uses and win.

        Sharing the ancestor's policies is sound because deriving is what
        establishes that the two speak the same policy contract; that is the
        check the `"<target>:<name>"` keying exists to make, and it still holds.
        """
        names = [self.name]
        anc = self.ancestor()
        if anc is not None:
            inherited = (anc.style_targets()
                         if isinstance(anc, OpModelTarget) else [anc.name])
            names += [n for n in inherited if n not in names]
        return names

    def resolve_style(self, name: str):
        """`--style name` for this target, searching the derivation chain."""
        from .style import StyleError, get as _get_style
        targets = self.style_targets()
        for target in targets:
            try:
                return _get_style(target, name)
            except StyleError as e:
                last = e
        raise StyleError(
            f"{last} (searched: {', '.join(targets)})"
            if len(targets) > 1 else str(last)) from None

    def available_styles(self) -> List[str]:
        """Every `--style` name that resolves here, including inherited ones."""
        from .style import list_styles
        seen: List[str] = []
        for target in self.style_targets():
            seen += [n for n in list_styles(target) if n not in seen]
        return sorted(seen)

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        """The options every operation-model target shares.

        Registered once and inherited, not restated per target. They are
        idempotent on the shared `compile` parser: the CLI's dedup proxy drops
        a repeat, so several targets in this family coexist.

        A DERIVED target gets its ancestor's options instead -- including the
        shared ones, which the ancestor's own chain adds -- so that a command
        line written for the ancestor works unchanged against the derived name.
        Skipped when the ancestor's class is already in this one's MRO, where
        `super()` has done it and doing it twice is an argparse conflict.
        """
        anc = self.ancestor()
        if anc is not None and not isinstance(self, type(anc)):
            anc.add_args(parser)
            return
        parser.add_argument(
            "--root", dest="progseq_root", metavar="COMP",
            help="op-model: root component type to generate the API for",
        )
        parser.add_argument(
            "--no-core-copy", dest="progseq_core_copy", action="store_false",
            default=True,
            help="op-model: do not copy the core seam header(s) into the "
                 "output dir",
        )
        parser.add_argument(
            "--ctor-name", dest="progseq_ctor_name", metavar="NAME",
            help="op-model: name of the `solve function` that is the "
                 "constructor (default: ctor, init or initialize)",
        )
        parser.add_argument(
            "--emit-manifest", dest="progseq_manifest", metavar="FILE",
            help="op-model: also write the elaborated model as JSON -- "
                 "components, operations and signatures, register offsets, "
                 "generated files and their roles. For a consumer that needs "
                 "to know what the API contains without parsing generated "
                 "code (see targets/manifest.py)",
        )

    # -- the run sequence ---------------------------------------------------

    def run(self, ctx, opts: argparse.Namespace) -> List[Path]:
        """elaborate -> check -> emit.

        The order is the contract. `check` runs before `emit` so that a model
        the backend cannot lower produces a diagnostic and NO FILES: a
        part-written artifact is worse than none, because a later incremental
        build treats it as up to date.
        """
        # `OpModel.ctor_names` is the authority and, since P6a.T5, the ONLY
        # thing any emitter reads: every `func_kind` call site takes the set
        # explicitly. The scope is still entered for the benefit of code
        # outside these emitters -- a plugin target, a tool, a test calling
        # `func_kind(fn)` on one function -- for which the ambient value is
        # still the fallback. Nothing in a generated file depends on it.
        from ..diag import Refusal
        from ..driver import CompileError
        with pm.ctor_names_scope(getattr(opts, "progseq_ctor_name", None)):
            try:
                model = self.build_model(ctx, opts)
                self.check(model)
                outputs = list(self.emit(model, opts))
            except Refusal as e:
                raise CompileError(
                    f"a construct cannot be lowered to "
                    f"{self.language or self.name}", [e.located()]) from e
            except OSError as e:
                raise _write_error(e) from e
            try:
                return outputs + self.emit_manifest(model, opts, outputs)
            except OSError as e:
                raise _write_error(e) from e

    def emit_manifest(self, model: OpModel, opts: argparse.Namespace,
                      outputs: Sequence[Path]) -> List[Path]:
        """`--emit-manifest`, for the whole family rather than per backend.

        AFTER `emit`, because the manifest records what was produced and in
        which order, and a list written before the files exist is a promise
        rather than a record. Its own path is not in it: a document does not
        describe itself, and a build system reading the manifest already has it.
        """
        path = getattr(opts, "progseq_manifest", None)
        if not path:
            return []
        from . import manifest as _manifest
        doc = _manifest.build(
            model, target=self.name, settings=self.abi_settings(opts),
            files=outputs, runtime_files=self.core_file_names(model, opts),
            include_files=self.include_only_files(outputs),
            enum_symbol=lambda e, item: self.enum_item_symbol(e, item, opts),
            extern=lambda dt: self.is_extern_type(dt, opts))
        return [_manifest.write(path, doc)]

    def enum_item_symbol(self, enum_dtype, item: str,
                         opts: argparse.Namespace) -> Optional[str]:
        """How the generated code spells *item* of *enum_dtype*, for the
        manifest; None if this target does not say."""
        return None

    def is_extern_type(self, dtype, opts: argparse.Namespace) -> bool:
        """Whether the generated code leaves ``dtype`` for the platform to
        declare (`StylePolicy.is_extern_type`), for the manifest. False
        here; a target with a style policy asks it."""
        return False

    def include_only_files(self, outputs: Sequence[Path]) -> List[str]:
        """The names of the ``outputs`` another file includes, which are
        never compiled on their own. None here; op-model-sv's `.svh`."""
        return []

    def abi_settings(self, opts: argparse.Namespace) -> Dict[str, Any]:
        """The options that change the generated API's SHAPE, for the manifest.

        Empty here, and empty is a legitimate answer: a backend with no option
        that moves a symbol, an offset or a signature has nothing to declare.
        The C target returns its `flags_for(opts)` -- link style, lifecycle,
        register style -- because each of those changes what a caller links
        against. A comment style would not belong.
        """
        return {}

    def ctor_names_for(self, opts: argparse.Namespace) -> FrozenSet[str]:
        """Which `solve function` names mean "constructor" under ``opts``."""
        name = getattr(opts, "progseq_ctor_name", None)
        return frozenset({name}) if name else pm.DEFAULT_CTOR_NAMES

    def build_model(self, ctx, opts: argparse.Namespace) -> OpModel:
        """`--root` resolved and the subtree elaborated -- `run`'s first step.

        Separate and public because a caller that wants to know what WOULD be
        generated (the differential test helper, a manifest dump) needs the
        model and must not have to restate how `--root` and `--ctor-name` are
        read. Restating it is how the two answers drift.
        """
        from .comp_inherit import complete
        from .export_action import entry_points
        # First: every reader below -- the entry-name clash check included --
        # sees a derived component with what it inherits (`comp_inherit`),
        # and a derived struct with its bases' members.
        _drop_constraint_functions(ctx)
        complete(ctx)
        pm.flatten_struct_bases(ctx)
        entries = entry_points(ctx, getattr(opts, "export_actions", None) or [])
        if entries and not self.supports_entries:
            raise ValueError(
                f"{self.name} does not yet render exported actions "
                f"(--export-action {entries[0].action}); op-model-py does")
        root_name = getattr(opts, "progseq_root", None)
        if not root_name and not entries:
            raise ValueError(f"{self.name} requires --root <component> or "
                             f"--export-action <action>")
        with pm.ctor_names_scope(getattr(opts, "progseq_ctor_name", None)):
            # Without --root, the tree is the one the first entry runs in.
            root = (self.resolve_root(ctx, root_name) if root_name
                    else entries[0].comp)
            return self.elaborate(ctx, root, opts,
                                  ctor_names=self.ctor_names_for(opts),
                                  entries=entries)

    def sections(self, model: OpModel,
                 opts: argparse.Namespace) -> Dict[str, str]:
        """`{"<file>:<section>": text}` -- what this run would generate,
        attributed to the section that produced it. Nothing is written.

        Empty here, and empty is a legitimate answer: a target that does not
        assemble from named sections has nothing to attribute, and the
        differential helper falls back to whole-file comparison. The C backend
        implements it (`c_progseq_tgt`).
        """
        return {}

    def elaborate(self, ctx, root, opts, *, ctor_names=None,
                  entries=()) -> OpModel:
        """Build the `OpModel`. Overridable, but there is rarely a reason."""
        out_dir = Path(str(getattr(opts, "output_dir", ".") or "."))
        return elaborate(ctx, root, out_dir, ctor_names=ctor_names,
                         entries=entries,
                         pkg_functions=self.supports_package_functions,
                         native_inheritance=self.native_inheritance)

    def check(self, model: OpModel) -> None:
        """Everything that must hold before any file is opened.

        The checks were all learned the same way -- from a build that
        exited 0 and produced something useless:

        * **Call legality.** A call the backend cannot lower is a compile error
          with a location, never emitted text. See `validate_calls`.
        * **A non-empty API.** An export API with zero operations is the shape
          every front-end defect in this generator's history took: the model
          translated, the file was written, the run exited 0, and it contained
          interfaces with nothing in them. Nobody reads a warning printed by a
          successful build.
        """
        from .unsupported_types import refuse
        from .validate_calls import gate
        refuse(model, self.unsupported_types, self.language or self.name)
        self.check_body_kinds(model)
        gate(model.root, model.ctx, self.legality_target or self.name,
             self.language or self.name, model.ctor_names,
             entries=[(e.comp, fn) for e in model.entries
                      for fn in e.functions],
             pkg_functions=self.supports_package_functions,
             init_blocks=self.supports_init_blocks,
             array_params=self.supports_array_params,
             string_methods=self.supports_string_methods,
             extra_components=model.base_classes,
             native=self.native_inheritance)
        self.check_executors(model)
        self.check_group_bindings(model)
        self.check_register_offsets(model)
        self.check_reserved_access(model)
        self.assert_api_is_not_empty(model)

    def body_emitter_class(self):
        """The class that renders this target's operation bodies, or None.

        `check_body_kinds` asks it which IR kinds it renders
        (`body_kinds.emitter_kinds`). A target that names none is not
        pre-screened, and an emitter meeting a kind it lacks is an internal
        error, as before P5."""
        return None

    def check_body_kinds(self, model: OpModel) -> None:
        """Refuse, with its location, every node of a rendered body that the
        body emitter has no rendering for (`body_kinds`): before, the
        emitter raised an internal error from the middle of emission."""
        cls = self.body_emitter_class()
        if cls is None:
            return
        from ..driver import CompileError
        from . import body_kinds
        from .validate_calls import rendered_bodies
        bodies = rendered_bodies(
            model.root, model.ctx, ctor_names=model.ctor_names,
            entries=[(e.comp, fn) for e in model.entries
                     for fn in e.functions],
            pkg_functions=self.supports_package_functions,
            init_blocks=self.supports_init_blocks,
            extra_components=model.base_classes,
            native=self.native_inheritance)
        language = self.language or self.name
        bad = body_kinds.refusals(bodies, body_kinds.emitter_kinds(cls),
                                  language)
        if bad:
            raise CompileError(
                f"{len(bad)} construct(s) cannot be lowered to {language}",
                bad)

    def check_register_offsets(self, model: OpModel) -> None:
        """Evaluate every register's offset (`reg_layout.collect_accessors`)
        before any file is opened. An offset function the build cannot run,
        or one that answers the -1 sentinel for a declared register, is the
        model's error and is reported as one, one line per component."""
        from ..diag import at
        from ..driver import CompileError
        from .reg_layout import collect_accessors

        bad = []
        for comp in {id(d): d for d in model.comp_dtypes}.values():
            try:
                collect_accessors(comp)
            except pm.OffsetFoldError as e:
                name = (getattr(comp, "name", "") or "?").split("::")[-1]
                bad.append(at((getattr(e, "node", None), comp),
                              f"{name}: {e}"))
        if bad:
            raise CompileError(
                f"{len(bad)} register offset(s) cannot be evaluated", bad)

    def check_reserved_access(self, model: OpModel) -> None:
        """Refuse a body that accesses a reserved register-group member
        (`reserved_access.check`): no backend surfaces one, and each used to
        lower the access by its own fallback -- an untyped C access, an SV
        member that failed only at elaboration."""
        from ..driver import CompileError
        from . import reserved_access
        from .validate_calls import rendered_bodies

        bodies = rendered_bodies(
            model.root, model.ctx, ctor_names=model.ctor_names,
            entries=[(e.comp, fn) for e in model.entries
                     for fn in e.functions],
            pkg_functions=False, init_blocks=True,
            extra_components=model.base_classes,
            native=self.native_inheritance)
        tm = getattr(model.ctx, "type_map", {}) or {}
        comps = list({id(d): d for d in model.comp_dtypes}.values())
        bad = reserved_access.check(bodies, comps, tm)
        if bad:
            raise CompileError(
                f"{len(bad)} access(es) to a reserved register", bad)

    def check_group_bindings(self, model: OpModel) -> None:
        """Refuse a register group that no `set_handle` reaches
        (`group_binding.check`): it has no address, and every backend used to
        give it someone else's."""
        from ..driver import CompileError
        from . import group_binding

        tm = getattr(model.ctx, "type_map", {}) or {}
        comps = list({id(d): d for d in model.comp_dtypes}.values())
        bad = group_binding.check(comps, model.ctor_names, tm,
                                  table=model.bindings)
        if bad:
            raise CompileError(
                f"{len(bad)} register group(s) are never bound", bad)

    def check_executors(self, model: OpModel) -> None:
        """Refuse an executor this backend would lower wrongly
        (`executors.check`)."""
        from ..driver import CompileError
        from . import executors

        tm = getattr(model.ctx, "type_map", {}) or {}
        bad = executors.check([n.dtype for n in model.components], tm,
                              delegating=self.supports_executor_delegation)
        if bad:
            raise CompileError(
                f"{len(bad)} executor(s) cannot be lowered to "
                f"{self.language or self.name}", bad)

    @staticmethod
    def assert_api_is_not_empty(model: OpModel) -> None:
        if model.total_operations() == 0:
            names = ", ".join(n.name for n in model.components)
            raise ValueError(
                f"the generated export API would contain zero operations "
                f"(components walked: {names or '(none)'}). Either --root "
                f"names a component with no operations, or the operations did "
                f"not survive translation -- check that `extend component` "
                f"bodies reached the IR.")

    @abc.abstractmethod
    def emit(self, model: OpModel, opts: argparse.Namespace) -> List[Path]:
        """Write the artifacts. THE method a backend implements."""

    # -- runtime source ------------------------------------------------------

    #: The distribution shipping this target's runtime source, and the
    #: `share/<lang>/` subdirectory holding it. A plugin sets `core_package` to
    #: its own package name and ships `share/<core_lang>/` as package data --
    #: which is the whole reason the lookup takes a package rather than
    #: hard-coding pssc's.
    core_package: str = "pssc"
    core_lang: str = ""

    def core_file_names(self, model: OpModel,
                        opts: argparse.Namespace) -> List[str]:
        """Which runtime files this generation needs, in copy order.

        Content-dependent by design: the C++ backend copies the channel header
        only for a model that has channels, and a directory carrying headers
        nothing includes invites the reader to wonder what is missing.
        """
        return []

    def install_core(self, model: OpModel,
                     opts: argparse.Namespace) -> List[Path]:
        """Copy the runtime source beside the generated output.

        One implementation for the family. It was three: SV, C and C++ each
        resolved a directory, looped, and copied, and each got its own chance to
        forget `--no-core-copy` or to fail unhelpfully on a file missing from
        the wheel.

        The CALLER decides where the copied files land in the returned path
        list, because that list is a compilation order and the answer differs:
        the SV core package must be compiled before the generated package that
        imports it, while a C header is included by name and can be written
        last.
        """
        if not getattr(opts, "progseq_core_copy", True):
            return []
        names = self.core_file_names(model, opts)
        if not names:
            return []
        from ..resources import core_files as _resolve
        lang = self.core_lang or self.name
        written: List[Path] = []
        model.out_dir.mkdir(parents=True, exist_ok=True)
        for src in _resolve(names, self.core_package, lang):
            dst = model.out_dir / src.name
            _install_copy(src, dst)
            written.append(dst)
        return written

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def resolve_root(ctx, root_name: str):
        """Resolve the root component datatype from the type table by name.

        Accepts a bare name (``wb_dma_c``) or a qualified one
        (``pkg::wb_dma_c``). Raises ValueError naming the candidates, because
        "unknown component" without a list is a question, not a diagnostic.
        """
        tm = getattr(ctx, "type_map", {}) or {}
        if root_name in tm:
            return tm[root_name]
        cands = [n for n in tm if n.split("::")[-1] == root_name]
        if len(cands) == 1:
            return tm[cands[0]]
        if len(cands) > 1:
            raise ValueError(
                f"ambiguous --root '{root_name}'; matches: "
                f"{', '.join(sorted(cands))}")
        comps = sorted({n for n, dt in tm.items()
                        if type(dt).__name__ == "DataTypeComponent"})
        raise ValueError(
            f"unknown --root '{root_name}'; available components: "
            + (", ".join(comps) or "(none)"))
