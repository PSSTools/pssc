"""Lower a PSS model to the OO interface-class projection (export API).

Emits, into the generated package, an interface-class API plus a factory so a
testbench can drive the model without knowing the runtime class layout:

    import_api_if  : pure-virtual protos for import target/solve functions
    import_api_base: concrete base stubbing every import method with $fatal
    export_api_if  : one pure-virtual task per export action
    factory_if     : create(import_api_if) -> export_api_if
    <root_comp>    : the root component class, *augmented* to also be the
                     factory (implements factory_if, static self / type_id(),
                     create()).
    export_api_impl: extends the root component and implements export_api_if;
                     one task per export action that runs the action lifecycle
                     with ``comp == this`` (the impl *is* the component).

Testbench usage -- type_id() returns the factory as a first-class handle that
can be passed around (a "constructor" for the export API):

    factory_if    f  = pss_top::type_id();
    export_api_if ep = f.create(imp);      // or: pss_top::type_id().create(imp)
    ep.Entry();

The runtime action/component classes are produced by the normal lowering
(``pss_to_sv``); this module only adds the API/factory/impl layer on top and
mutates the root component class in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from zuspec.be.sv.ir.sv import (
    SVArg,
    SVClass,
    SVClassField,
    SVFunctionDecl,
    SVInterfaceClass,
    SVTaskDecl,
)

from .lower_top import emit_root_action_lifecycle


@dataclass
class ExportAction:
    """One action exposed on ``export_api_if``.

    Attributes:
        task_name:    SV task name on the export API (PSS action short name).
        action_sv:    Mangled SV class name of the action (e.g. pss_top__Entry).
        comp_sv:      Mangled SV class name of the enclosing component.
        has_activity: True for compound actions (call ``activity()``), else
                      atomic (call ``body()``).
    """
    task_name: str
    action_sv: str
    comp_sv: str
    has_activity: bool = True


# Names of the generated interface classes / factory / impl (fixed).
IMPORT_API_IF = "import_api_if"
IMPORT_API_BASE = "import_api_base"  # base impl with stubbed (fatal) methods
EXPORT_API_IF = "export_api_if"
FACTORY_IF = "factory_if"            # interface for the export-API factory
EXPORT_API_IMPL = "export_api_impl"
ACTION_RUN_IF = "pss_action_run_if"  # core generic-run facade (in zsp_rt_pkg)


def _import_sig(ctx, f):
    """Return ``(is_task, sv_args, ret_type)`` for an import function.

    A ``target`` function models a SUT call that may consume time, so it maps
    to an SV **task** -- a return value, if any, comes back through a leading
    ``output`` argument (``ret``); ``solve`` functions map to an SV
    **function**.
    """
    args = []
    for a in (f.args.args if getattr(f, "args", None) else []):
        dtype = ctx.pss_type_to_sv_type_str(a.annotation) if a.annotation is not None else "int"
        args.append(SVArg(name=a.arg, dtype=dtype))
    is_task = bool(getattr(f, "is_target", False))
    ret = ctx.pss_type_to_sv_type_str(f.returns) if f.returns is not None else "void"
    if is_task and ret != "void":
        # A target import may consume time, so it is a task even when it
        # returns a value: the value comes back through a leading output.
        out = "ret"
        while any(a.name == out for a in args):
            out += "_"
        args = [SVArg(name=out, dtype=ret, direction="output")] + args
    return is_task, args, ret


def _lower_import_api_if(ctx, import_funcs) -> SVInterfaceClass:
    """Build the import API interface class.

    One pure-virtual prototype per package-scope ``import target``/``import
    solve`` function (``target``-void -> task, otherwise function); the
    testbench supplies the implementation.
    """
    tasks, funcs = [], []
    for f in (import_funcs or []):
        is_task, args, ret = _import_sig(ctx, f)
        if is_task:
            tasks.append(SVTaskDecl(name=f.name, args=args))
        else:
            funcs.append(SVFunctionDecl(name=f.name, args=args, return_type=ret))
    return SVInterfaceClass(name=IMPORT_API_IF, tasks=tasks, functions=funcs)


def _lower_import_api_base(ctx, import_funcs) -> SVClass:
    """Build ``import_api_base``: a concrete base implementing every import
    method as a stub that ``$fatal``s if reached.

    User/testbench code extends this and overrides only the methods it needs
    (e.g. via parameterized mixins), so unimplemented imports fail loudly at
    runtime rather than silently.
    """
    cls = SVClass(name=IMPORT_API_BASE, implements=[IMPORT_API_IF])
    for f in (import_funcs or []):
        is_task, args, ret = _import_sig(ctx, f)
        stub = f'$fatal(1, "{f.name} not implemented");'
        if is_task:
            cls.tasks.append(SVTaskDecl(
                name=f.name, args=args, is_virtual=True, body_lines=[stub]))
        else:
            body = [stub]
            if ret != "void":
                body.append(f"return {_zero_value(ret)};")  # unreachable; satisfies SV
            cls.functions.append(SVFunctionDecl(
                name=f.name, args=args, return_type=ret,
                is_virtual=True, body_lines=body))
    return cls


def _zero_value(sv_type: str) -> str:
    """A trivial default value for *sv_type* (for unreachable stub returns)."""
    return "0"


def _lower_export_api_if(actions: List[ExportAction]) -> SVInterfaceClass:
    """Build ``export_api_if`` with one pure-virtual task per export action."""
    return SVInterfaceClass(
        name=EXPORT_API_IF,
        tasks=[SVTaskDecl(name=a.task_name) for a in actions],
    )


def _lower_factory_if() -> SVInterfaceClass:
    """Build ``factory_if``: the export-API factory interface.

    Because it is an interface realised by an *instance* (the root component),
    a ``factory_if`` handle can be passed around as a first-class "constructor"
    for the export API: ``factory_if f = pss_top::type_id(); f.create(imp);``.
    """
    return SVInterfaceClass(
        name=FACTORY_IF,
        functions=[SVFunctionDecl(
            name="create",
            args=[SVArg(name="imp_if", dtype=IMPORT_API_IF)],
            return_type=EXPORT_API_IF,
        )],
    )


def _lower_export_api_impl(actions: List[ExportAction],
                           has_imports: bool = False) -> SVClass:
    """Build ``export_api_impl`` implementing ``export_api_if``.

    The impl **is** the root component: it ``extends`` the root component class,
    so a running action's ``comp`` is ``this`` (the impl instance) -- there is
    no separately-constructed component tree.  The constructor wires the import
    handle onto ``this`` so action exec bodies reach it via ``comp.import_if``.

    (If export actions span multiple components -- no single root -- the impl
    instead constructs a fresh component per action; see the fallback below.)
    """
    comp_svs = {a.comp_sv for a in actions}
    single_comp = next(iter(comp_svs)) if len(comp_svs) == 1 else None

    if single_comp is not None:
        cls = SVClass(name=EXPORT_API_IMPL, extends_name=single_comp,
                      implements=[EXPORT_API_IF])
        new_body = ['super.new("top", null);']
        if has_imports:
            # import_if is inherited from the root component class;
            # set_import_if hands it down the component tree.
            new_body.append("set_import_if(imp_if);")
        cls.functions.append(SVFunctionDecl(
            name="new",
            args=[SVArg(name="imp_if", dtype=IMPORT_API_IF)],
            return_type="",  # constructors have no return type
            body_lines=new_body,
        ))
        for a in actions:
            # comp == this: the impl is the component.
            body = emit_root_action_lifecycle(
                a.action_sv, a.has_activity, indent="", comp_ref="this")
            cls.tasks.append(SVTaskDecl(name=a.task_name, body_lines=body))
        return cls

    # Fallback: export actions span multiple components -> construct a fresh
    # component tree per action and wire the import handle onto it.
    cls = SVClass(name=EXPORT_API_IMPL, implements=[EXPORT_API_IF])
    cls.fields.append(SVClassField(name="m_imp", dtype=IMPORT_API_IF))
    cls.functions.append(SVFunctionDecl(
        name="new",
        args=[SVArg(name="imp_if", dtype=IMPORT_API_IF)],
        return_type="",
        body_lines=["m_imp = imp_if;"],
    ))
    for a in actions:
        body = [f'automatic {a.comp_sv} top = new("top", null);']
        if has_imports:
            body.append("top.set_import_if(m_imp);")
        body.extend(emit_root_action_lifecycle(a.action_sv, a.has_activity,
                                                indent=""))
        cls.tasks.append(SVTaskDecl(name=a.task_name, body_lines=body))
    return cls


def _lower_action_runners(actions: List[ExportAction]) -> List[SVClass]:
    """Build one ``<action>_runner`` per export action (Command pattern).

    Each binds an ``export_api_if`` handle and exposes the uniform
    ``pss_action_run_if::run()`` (which calls the bound export task), plus a
    static ``create(export_api_if)`` matching the factory idiom. Lets a
    testbench build ``pss_action_run_if program[$]`` and dispatch a
    heterogeneous, ordered list of invocations polymorphically.
    """
    runners: List[SVClass] = []
    for a in actions:
        name = f"{a.task_name}_runner"
        cls = SVClass(name=name, implements=[ACTION_RUN_IF])
        cls.fields.append(SVClassField(name="m_api", dtype=EXPORT_API_IF))
        cls.functions.append(SVFunctionDecl(
            name="new",
            args=[SVArg(name="ep", dtype=EXPORT_API_IF)],
            return_type="",
            body_lines=["m_api = ep;"],
        ))
        cls.tasks.append(SVTaskDecl(
            name="run", is_virtual=True,
            body_lines=[f"m_api.{a.task_name}();"]))
        cls.functions.append(SVFunctionDecl(
            name="create",
            args=[SVArg(name="ep", dtype=EXPORT_API_IF)],
            return_type=ACTION_RUN_IF,
            is_static=True,
            body_lines=[f"{name} self = new(ep);", "return self;"],
        ))
        runners.append(cls)
    return runners


def _augment_root_factory(comp_cls: SVClass) -> None:
    """Make the root component class *be* the export-API factory, in place.

    Adds ``implements factory_if``, a static singleton + ``type_id()`` accessor,
    and a virtual ``create()`` returning a fresh ``export_api_impl``.  Because
    ``type_id()`` returns the component instance (which is-a ``factory_if``), a
    testbench can hold and pass the factory around::

        factory_if    f  = pss_top::type_id();
        export_api_if ep = f.create(imp);
        ep.Entry();
    """
    if FACTORY_IF not in comp_cls.implements:
        comp_cls.implements = list(comp_cls.implements) + [FACTORY_IF]
    # export_api_impl is defined later in the package -> forward declare.
    if EXPORT_API_IMPL not in comp_cls.forward_decls:
        comp_cls.forward_decls = list(comp_cls.forward_decls) + [EXPORT_API_IMPL]

    comp_cls.fields.insert(0, SVClassField(
        name="self", dtype=comp_cls.name, is_static=True))

    comp_cls.functions.append(SVFunctionDecl(
        name="type_id",
        return_type=comp_cls.name,
        is_static=True,
        body_lines=[
            'if (self == null) self = new("factory", null);',
            "return self;",
        ],
    ))
    comp_cls.functions.append(SVFunctionDecl(
        name="create",
        args=[SVArg(name="imp_if", dtype=IMPORT_API_IF)],
        return_type=EXPORT_API_IF,
        is_virtual=True,
        body_lines=[
            f"{EXPORT_API_IMPL} ret = new(imp_if);",
            "return ret;",
        ],
    ))


def _wire_import_if(sv_nodes: List, ctx) -> None:
    """Give every component class an ``import_if`` handle and a
    ``set_import_if`` that hands it down to the sub-components, so a
    component's own function body can reach the import API. The export-API
    impl calls ``set_import_if`` on the root."""
    children = getattr(ctx, "component_children", {}) or {}
    for n in sv_nodes:
        if not isinstance(n, SVClass) or n.name not in children:
            continue
        body = []
        if n.extends_name and n.extends_name in children:
            body.append("super.set_import_if(imp);")
        else:
            existing = [f for f in n.fields if f.name == "import_if"]
            if not existing:
                n.fields.append(SVClassField(name="import_if", dtype=IMPORT_API_IF))
            if not existing or existing[0].dtype == IMPORT_API_IF:
                body.append("import_if = imp;")
        for c in children[n.name]:
            body.append(f"if ({c} != null) {c}.set_import_if(imp);")
        n.functions.append(SVFunctionDecl(
            name="set_import_if",
            args=[SVArg(name="imp", dtype=IMPORT_API_IF)],
            return_type="void",
            is_virtual=True,
            body_lines=body,
        ))


def build_oo_api_nodes(
    sv_nodes: List,
    actions: List[ExportAction],
    ctx=None,
    import_funcs=None,
) -> Tuple[List, List]:
    """Build the OO-projection nodes and augment the root component class.

    Finds each export action's component class within *sv_nodes* and augments
    the (single) root component into the factory.  Returns ``(prefix, suffix)``
    node lists to splice around *sv_nodes* in the package:

        package = prefix + sv_nodes + suffix

    Raises:
        ValueError: if a root component class is not found among *sv_nodes*.
    """
    if not actions:
        return [], []

    # Augment the root component class(es) referenced by the export actions.
    root_comp_names = {a.comp_sv for a in actions}
    found = {
        n.name for n in sv_nodes
        if isinstance(n, SVClass) and n.name in root_comp_names
    }
    missing = root_comp_names - found
    if missing:
        raise ValueError(
            f"OO projection: root component class(es) {sorted(missing)} not "
            f"found among generated SV nodes."
        )
    has_imports = bool(import_funcs)
    if has_imports and ctx is not None:
        _wire_import_if(sv_nodes, ctx)
    for n in sv_nodes:
        if isinstance(n, SVClass) and n.name in root_comp_names:
            _augment_root_factory(n)
            # Give the root component a handle to the import API so action
            # exec bodies can reach it via comp.import_if.<fn>(...).
            if has_imports and not any(f.name == "import_if" for f in n.fields):
                n.fields.append(SVClassField(name="import_if", dtype=IMPORT_API_IF))
            if has_imports and not any(f.name == "set_import_if" for f in n.functions):
                # Not wired by _wire_import_if (no component info): the
                # root alone holds the handle.
                n.functions.append(SVFunctionDecl(
                    name="set_import_if",
                    args=[SVArg(name="imp", dtype=IMPORT_API_IF)],
                    return_type="void", is_virtual=True,
                    body_lines=["import_if = imp;"]))

    prefix = [_lower_import_api_if(ctx, import_funcs)]
    if has_imports:
        # base impl with stubbed (fatal) methods for the testbench to extend
        prefix.append(_lower_import_api_base(ctx, import_funcs))
    prefix.extend([
        _lower_export_api_if(actions),
        _lower_factory_if(),
    ])
    suffix = [_lower_export_api_impl(actions, has_imports=has_imports)]
    suffix.extend(_lower_action_runners(actions))
    return prefix, suffix
