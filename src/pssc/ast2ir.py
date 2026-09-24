"""
AST to IR Translation Module

Translates PSS AST nodes to Zuspec IR (Intermediate Representation).
"""
from __future__ import annotations
import contextlib
import copy
import dataclasses
import enum
import logging
from typing import Dict, List, Optional, Any, Set, Tuple, TYPE_CHECKING
import zuspec.ir.core as ir

if TYPE_CHECKING:
    import pssparser.ast as pss_ast
else:
    import pssparser.ast as pss_ast


def ast_comments(node: Any) -> Tuple[Optional[str], Optional[str]]:
    """The ``(leading, trailing)`` comment text attached to a PSS AST node.

    Empty when the parser was not asked to collect comments, which is what
    ``--no-comments`` produces.

    Orphans -- comments a blank line detached from any construct -- are
    deliberately dropped. That is the mechanism by which a file note above the
    imports stays out of the generated code, and by which an author suppresses
    propagation of any one comment. See docs/design/pss-comment-propagation-plan.md.
    """
    getter = getattr(node, "getComments", None)
    if getter is None:
        return (None, None)

    leading: List[str] = []
    trailing: List[str] = []
    for c in getter():
        placement = c.getPlacement()
        if placement == pss_ast.CommentPlacement.CommentPlacement_Leading:
            leading.append(c.getText())
        elif placement == pss_ast.CommentPlacement.CommentPlacement_Trailing:
            trailing.append(c.getText())

    return ("\n".join(leading) or None, "\n".join(trailing) or None)


def _ast_where(node: Any) -> str:
    """``line N: `` for an AST node the parser located, else ``''``."""
    try:
        line = node.getLocation().lineno
    except Exception:
        return ""
    return f"line {line}: " if line and line > 0 else ""


def ast_doc(node: Any) -> Optional[str]:
    """The documentation for a declaration: its leading comment, else trailing.

    Matches what the parser puts in ``docstring``, but reads the comment list
    so a caller that collected comments without docstrings still gets it.
    """
    leading, trailing = ast_comments(node)
    return leading if leading is not None else trailing


@dataclasses.dataclass(frozen=True)
class _GenericRef:
    """A generic constraint declaration visible from some referencing type.

    ``foreign`` marks a declaration whose ``self`` is not the referencing type's
    -- a component- or package-scope one. ``scope`` is the declaring scope's
    name, carried only so a diagnostic can say where the declaration was.
    ``is_value`` distinguishes the value-yielding form (§13.1.2 b), which
    contributes a value to the expression around it, from the boolean form,
    which is a constraint in its own right.
    """
    fn: 'ir.Function'
    foreign: bool
    scope: str
    is_value: bool = False


class _Phase(enum.Enum):
    """The elaboration passes over the unit list.

    CONST records the package- and global-scope constants and enums that other
    declarations fold against; DECLARE registers every type declaration; EXTEND
    applies every `extend` to the types DECLARE registered. Splitting them is
    what makes translation independent of the order the files were presented in
    -- see AstToIrTranslator._translate_global_scope.
    """
    CONST = 0
    DECLARE = 1
    EXTEND = 2


class AstToIrContext:
    """Context for AST to IR translation

    Maintains state during translation including:
    - Type registry (name -> IR DataType)
    - Symbol tables for scope resolution
    - Error collection
    - Current scope tracking
    """

    def __init__(self):
        self.type_map: Dict[str, ir.DataType] = {}
        self.symbol_table: Dict[str, Any] = {}
        self.errors: List[str] = []
        self.scope_stack: List[ir.DataType] = []
        self.ir_context: Optional[ir.Context] = None
        # Names declared as template parameters somewhere in the model. A
        # DataTypeRef naming one of these is a parameter placeholder, not an
        # unresolved type, so the completeness check below must not report it.
        self.template_param_names: Set[str] = set()
        # Maps qualified action name ('MyC::MyA') -> parent component name ('MyC')
        self.parent_comp_names: Dict[str, str] = {}
        # Set of local variable names in the current scope (e.g. foreach loop vars)
        self.local_vars: set = set()
        # Package/global-scope `import target/solve function` declarations,
        # surfaced so the SV backend can expose each on `import_api_if` and route
        # exec-body calls through the import handle.
        self.import_functions: List[ir.Function] = []
        # Package- and global-scope function *definitions*, by qualified and by
        # bare name (`p::f` and `f`). A package is only a namespace prefix here,
        # so these have no IR type to live on; without this map they were
        # dropped, and every call to one reached the backend unresolvable.
        self.functions: Dict[str, ir.Function] = {}
        # PSS name -> IR name for a local that shadows an outer one (20.7.1). A
        # nested block is flattened into its parent's statement list, so the
        # inner declaration gets a fresh name for the extent of its block.
        self.local_renames: Dict[str, str] = {}
        self.local_rename_seq: int = 0
        # The parameters of the function whose body is being translated. A
        # reference to one is `self.<name>` in the IR, not a local, so a local
        # of the same name must be told apart by its IR name (`_bind_local`).
        self.param_names: Set[str] = set()
        # Folded package-scope `static const` integers, by bare and qualified
        # name. Consumed where a compile-time constant must become a number --
        # array sizes, today.
        self.const_map: Dict[str, int] = {}
        # Package- and global-scope generic constraints, by qualified name
        # (`p::lt`). A package is only a namespace prefix in this translator --
        # it has no IR type -- so a generic constraint declared in one has
        # nowhere else to live, and without this it was dropped outright.
        self.generic_constraints: Dict[str, ir.Function] = {}
        # The linked root symbol scope. A reference the linker resolved is a
        # path of child indices from here; see `_package_function_at`.
        self.symbol_root = None
        # The generic constraint whose body is being translated, or None. Set
        # only so a body item that is illegal *inside* a generic constraint can
        # be reported by name -- §13.3 g forbids `default` there, and nowhere
        # else.
        self.generic_constraint_name: Optional[str] = None
        # How many generic constraint references have been instantiated so far.
        # Each reference gets the next number, which is what makes two
        # references to one declaration distinguishable after their bodies have
        # been spliced into the same constraint -- see
        # AstToIrTranslator._generic_provenance.
        self.generic_ref_sites: int = 0

    def push_scope(self, scope: ir.DataType):
        """Push a new scope (component, struct, etc.)"""
        self.scope_stack.append(scope)

    def pop_scope(self) -> Optional[ir.DataType]:
        """Pop the current scope"""
        if self.scope_stack:
            return self.scope_stack.pop()
        return None

    def current_scope(self) -> Optional[ir.DataType]:
        """Get the current scope"""
        return self.scope_stack[-1] if self.scope_stack else None

    #: Scalar names this translator owns outright.  The parser's builtin prelude
    #: declares `string` as a type scope (it carries the string methods), and
    #: translating that scope would otherwise displace the scalar IR type every
    #: backend expects under this name.
    _BUILTIN_SCALARS = frozenset({"bool", "int", "string"})

    def add_type(self, name: str, dtype: ir.DataType):
        """Register a type in the type map.

        Re-registering one of the builtin scalar names is ignored: the mapping
        installed by ``_init_builtin_types`` wins.
        """
        if name in self._BUILTIN_SCALARS and name in self.type_map:
            return
        self.type_map[name] = dtype

    def get_type(self, name: str) -> Optional[ir.DataType]:
        """Look up a type by name"""
        return self.type_map.get(name)

    def add_error(self, message: str):
        """Record a translation error"""
        self.errors.append(message)


class AstToIrTranslator:
    """Main translator from PSS AST to Zuspec IR

    Provides methods to translate:
    - Global scope (root node)
    - Components
    - Actions
    - Structs
    - Functions
    - Statements
    - Expressions
    - Data types
    """

    def __init__(self, debug: bool = False):
        """Initialize translator

        Args:
            debug: Enable debug logging
        """
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        if debug:
            self.logger.setLevel(logging.DEBUG)
        self._type_chain_stack: list = []  # enclosing type names during traversal

    def translate(self, ast_root: pss_ast.GlobalScope) -> AstToIrContext:
        """Translate the entire AST to IR.

        Args:
            ast_root:    Root AST node (GlobalScope)
        Returns:
            Translation context with IR and type registry
        """
        self._type_chain_stack = []

        ctx = AstToIrContext()
        ctx.symbol_root = ast_root

        # Initialize built-in types
        self._init_builtin_types(ctx)

        # Translate global scope
        self._translate_global_scope(ctx, ast_root)

        # Instantiate generic constraint references (PSS 3.1 §13.1.2). Done over
        # the whole context rather than per type as each finished translating,
        # because a reference may name a declaration in a scope this type does
        # not contain -- a base type, the enclosing component, a package -- and
        # that scope is only guaranteed to be translated once everything is.
        # Per-type expansion silently left `action A : Base { constraint c {
        # g(); } }` unexpanded whenever Base was declared later in the file.
        self._inline_all_generic_constraints(ctx)

        # Reduce the masked / field-wise register writes (PSS 3.1 §21.14.1) to
        # the single `write_val_masked` primitive. Here rather than in the
        # driver so that *every* consumer of a translated context sees the
        # reduced form -- a backend must never be handed a field name.
        from . import reg_rmw
        reg_rmw.reduce(ctx)

        return ctx

    def _init_builtin_types(self, ctx: AstToIrContext):
        """Initialize built-in types in the type registry"""
        # bool (represented as 1-bit unsigned int)
        bool_type = ir.DataTypeInt(name="bool", bits=1, signed=False)
        ctx.add_type("bool", bool_type)

        # int (32-bit signed)
        int_type = ir.DataTypeInt(name="int", bits=32, signed=True)
        ctx.add_type("int", int_type)

        # string
        string_type = ir.DataTypeString(name="string")
        ctx.add_type("string", string_type)

        # Common bit types
        for width in [8, 16, 32, 64]:
            bit_type = ir.DataTypeInt(name=f"bit[{width}]", bits=width, signed=False)
            ctx.add_type(f"bit[{width}]", bit_type)

            int_type = ir.DataTypeInt(name=f"int[{width}]", bits=width, signed=True)
            ctx.add_type(f"int[{width}]", int_type)

    def _translate_global_scope(self, ctx: AstToIrContext, global_scope):
        """Translate the global scope

        Args:
            ctx: Translation context
            global_scope: Root AST node (RootSymbolScope)
        """
        if self.debug:
            self.logger.debug("Translating global scope")

        # RootSymbolScope contains units (GlobalScope), iterate through them.
        #
        # Three passes, because PSS 3.1 18.2 makes file order semantically
        # irrelevant: "most elements may be referenced before their declaration".
        # A single pass resolved each construct against whatever had been
        # translated so far, which cost two distinct silent losses:
        #
        #   - `extend component leaf_c { action a {...} }` listed before leaf_c's
        #     own file found no target and dropped the action. On this project's
        #     companion-directory layout (every action lives in an `extend` file)
        #     an alphabetical file order lost all 13 actions and 38% of the IR.
        #   - `leaf_c ch[WB_DMA_MAX_CH]` whose const lives in a later file folded
        #     to `size: -1` -- an array that quietly sizes to nothing.
        #
        # Recording constants first, then declaring every type, then applying
        # every extension makes the result a function of the file *set* rather
        # than its order. Order within a pass is still file order, which is all
        # 18.2 permits a tool to require (a const initializer naming another
        # const is the legitimate case -- see file-order-probes.md Family F).
        for phase in (_Phase.CONST, _Phase.DECLARE, _Phase.EXTEND):
            for i in range(global_scope.numUnits()):
                unit = global_scope.getUnit(i)
                self._translate_unit(ctx, unit, phase=phase)

        self._resolve_subcomponent_refs(ctx)
        self._check_refs_resolve(ctx)

    def _resolve_subcomponent_refs(self, ctx: AstToIrContext) -> None:
        """A sub-component field typed by a FORWARD reference gets the type a
        backward one does.

        A field whose type is declared earlier holds the `DataTypeComponent`
        itself; one whose type is declared later -- further down the file, or
        in a later file -- held a `DataTypeRef`, and every consumer that walks
        the component tree by field type (`progseq_model.sub_components`)
        passed it over. The sub-component was silently absent from the model:
        `component top { sub_c s; } component sub_c {...}` lowered with no `s`.

        Only component-typed fields of components, and elements of arrays of
        them, are resolved. Other references keep their indirection -- an
        action-handle field is a `DataTypeRef` on purpose (a reference, not
        an embedded value), and a template parameter is not a type yet.
        """
        def resolve(dt):
            if not isinstance(dt, ir.DataTypeRef):
                return dt
            name = dt.ref_name
            if not name or name in ctx.template_param_names:
                return dt
            found = ctx.get_type(name)
            if found is None and "::" in name:
                found = ctx.get_type(name.rsplit("::", 1)[-1])
            return found if isinstance(found, ir.DataTypeComponent) else dt

        seen: Set[int] = set()
        for dt in list(ctx.type_map.values()):
            if id(dt) in seen or not isinstance(dt, ir.DataTypeComponent):
                continue
            seen.add(id(dt))
            for f in getattr(dt, "fields", None) or []:
                f.datatype = resolve(f.datatype)
                if isinstance(f.datatype, ir.DataTypeArray):
                    f.datatype.element_type = resolve(f.datatype.element_type)

    def _check_refs_resolve(self, ctx: AstToIrContext) -> None:
        """Every DataTypeRef in the IR must name a type the model declares.

        A `DataTypeRef` is the translator's deliberate indirection -- super
        types always use one, and a field type may forward-reference a type
        declared later. That is fine as long as the name resolves *by the end*.
        One that never resolves is a reference the backends will follow to
        nothing, so it is reported here rather than discovered as a missing
        emission downstream.

        This is the structural half of the fix: the passes above remove the
        known order dependencies, and this check is what notices if some other
        path drops a type. Without it, "no error" only means "nobody looked".
        """
        # Resolve by the same rule the backends use (see targets/sv/context.py:
        # exact key, then any key ending "::<name>"), so this never reports a
        # reference a consumer would in fact have followed. The last-segment
        # index also covers the instance-path form (`tx::send_pkt`) without
        # reimplementing it -- deliberately generous, because a check that
        # over-reports on valid models gets switched off.
        by_last_segment: Set[str] = {
            key.rsplit("::", 1)[-1] for key in ctx.type_map
        }

        def resolves(name: str) -> bool:
            return (name in ctx.type_map
                    or name in ctx.template_param_names
                    or name.rsplit("::", 1)[-1] in by_last_segment)

        seen: Set[int] = set()
        unresolved: Dict[str, int] = {}

        def walk(node, depth=0):
            if node is None or depth > 24 or id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, ir.DataTypeRef):
                if not resolves(node.ref_name):
                    unresolved[node.ref_name] = unresolved.get(node.ref_name, 0) + 1
                return
            for attr in ("types", "functions", "fields", "datatype", "super",
                         "body", "value", "container", "params", "return_type"):
                child = getattr(node, attr, None)
                if isinstance(child, (list, tuple)):
                    for elem in child:
                        walk(elem, depth + 1)
                elif child is not None:
                    walk(child, depth + 1)

        for dtype in list(ctx.type_map.values()):
            walk(dtype)

        for name in sorted(unresolved):
            ctx.add_error(
                f"unresolved type reference '{name}' "
                f"({unresolved[name]} use(s)): no declaration for it was found")

    def _translate_unit(self, ctx: AstToIrContext, unit, namespace_prefix: str = "",
                        phase: '_Phase' = None):
        """Translate a global scope unit

        Args:
            ctx: Translation context
            unit: GlobalScope unit
            namespace_prefix: Dot-separated namespace prefix for types inside packages
            phase: which elaboration pass to run (see _translate_global_scope).
                CONST records constants and enums, DECLARE registers type
                declarations, EXTEND applies extensions to what DECLARE
                registered.
        """
        if phase is None:
            phase = _Phase.DECLARE

        # Use children() method which returns an iterable
        for child in unit.children():
            if child is None:
                continue

            # A package body holds constants, declarations and extensions
            # alike, so it is walked in every pass.
            if isinstance(child, pss_ast.PackageScope):
                self._translate_package(ctx, child, namespace_prefix, phase=phase)
                continue

            # A package-scope generic constraint is recorded in the CONST pass,
            # before anything that could reference it is translated. Unlike a
            # type it has no IR home of its own to be found in later: a
            # reference is resolved against ctx.generic_constraints at the point
            # the referencing expression is translated, so the registry has to
            # be complete first, regardless of the order the files were given in.
            if isinstance(child, (pss_ast.GenericConstraintDeclBool,
                                  pss_ast.GenericConstraintDeclValue)):
                if phase is _Phase.CONST:
                    self._record_scope_generic_constraint(
                        ctx, child, namespace_prefix)
                continue

            # A package- or global-scope function definition. Translated in the
            # EXTEND pass, when every type its body may name is registered.
            if isinstance(child, pss_ast.FunctionDefinition):
                if phase is _Phase.EXTEND:
                    self._record_scope_function(ctx, child, namespace_prefix)
                continue

            kind = self._decl_phase(child)
            if kind is not phase:
                continue

            if isinstance(child, pss_ast.ExtendType):
                self._translate_extend(ctx, child)
            elif isinstance(child, pss_ast.ExtendEnum):
                self._translate_extend_enum(ctx, child)
            elif isinstance(child, pss_ast.Component):
                self._translate_component(ctx, child, namespace_prefix=namespace_prefix)
            elif isinstance(child, pss_ast.Action):
                self._translate_action(ctx, child, namespace_prefix=namespace_prefix)
            elif isinstance(child, pss_ast.Struct):
                self._translate_struct(ctx, child, namespace_prefix=namespace_prefix)
            elif isinstance(child, pss_ast.EnumDecl):
                self._translate_enum(ctx, child)
            elif isinstance(child, pss_ast.TypedefDeclaration):
                self._translate_typedef(ctx, child)
            elif isinstance(child, pss_ast.FunctionImportProto):
                self._translate_import_proto(ctx, child)
            elif isinstance(child, pss_ast.Field):
                # A package- or global-scope field is a `static const`. Its
                # value is folded so it can size an array: `wb_dma_ch_c
                # ch[WB_DMA_MAX_CH]` must reach the IR as a 4-element array, not
                # as `size=-1`, or nothing downstream can emit the accessors or
                # unroll the constructor loop.
                self._record_const(ctx, child, namespace_prefix)

    def _record_template_params(self, ctx: AstToIrContext, decl) -> None:
        """Note the template parameter names a type declaration introduces.

        `struct packed_s <endianness_e E = LITTLE>` puts `E` into the IR as a
        DataTypeRef wherever the body uses it. It names a parameter, not a type,
        so _check_refs_resolve must not report it as unresolved.
        """
        params = decl.getParams() if hasattr(decl, 'getParams') else None
        if params is None:
            return
        for i in range(params.numParams()):
            param = params.getParam(i)
            if param is None:
                continue
            name_node = param.getName()
            name = (name_node.getId() if hasattr(name_node, 'getId')
                    else str(name_node))
            if name:
                ctx.template_param_names.add(name)

    @staticmethod
    def _decl_phase(child) -> '_Phase':
        """Which pass a unit-level child belongs to.

        Constants and enums go first because other declarations fold against
        them (an array size, an enum item in a const initializer). Extensions go
        last because they need their target declared. Everything else is a
        declaration.
        """
        if isinstance(child, (pss_ast.ExtendType, pss_ast.ExtendEnum)):
            return _Phase.EXTEND
        if isinstance(child, (pss_ast.EnumDecl, pss_ast.Field)):
            return _Phase.CONST
        return _Phase.DECLARE

    def _record_const(self, ctx: AstToIrContext, field, namespace_prefix: str = "") -> None:
        """Fold a package-scope ``static const`` with a constant initializer.

        Recorded under both the bare and the qualified name, matching how types
        are registered, so `WB_DMA_MAX_CH` and `wb_dma_regs_pkg::WB_DMA_MAX_CH`
        both resolve. Non-constant initializers are skipped rather than guessed
        at -- an unfolded constant leaves the array size at -1, which is loud
        downstream, while a wrong fold would not be.
        """
        name_node = field.getName()
        name = name_node.getId() if isinstance(name_node, pss_ast.ExprId) else str(name_node)
        init = field.getInit() if hasattr(field, 'getInit') else None
        value = getattr(init, 'getValue', lambda: None)() if init is not None else None
        if not isinstance(value, int) or isinstance(value, bool):
            return
        ctx.const_map[name] = value
        if namespace_prefix:
            ctx.const_map[f"{namespace_prefix}{name}"] = value

    def _record_scope_generic_constraint(self, ctx: AstToIrContext, decl,
                                         namespace_prefix: str = "") -> None:
        """Record a package- or global-scope generic constraint (§13.1.2).

        Registered under its qualified name only (`p::lt`), because that is the
        only way PSS lets one be referenced from outside its package -- and a
        bare-name entry would let a reference resolve to a package the referencing
        scope never imported.

        Package-scope generic constraints "are always static" (§13.1.2), so the
        body may use nothing but its parameters. That is not checked here: it is
        checked where it matters, at each reference, so the diagnostic can name
        the referencing site -- see :meth:`_expand_generic_refs`.
        """
        # The two forms spell their name differently: the boolean form inherits
        # `ConstraintBlock`'s plain-string name, the value form carries an ExprId.
        name_node = decl.getName() if hasattr(decl, 'getName') else None
        if isinstance(name_node, pss_ast.ExprId):
            name = name_node.getId()
        elif isinstance(name_node, str):
            name = name_node
        else:
            return
        if not name:
            return
        if isinstance(decl, pss_ast.GenericConstraintDeclValue):
            fn = self._translate_generic_value_constraint(ctx, decl)
        else:
            # `owner` is only consulted to auto-name an anonymous block, and a
            # generic constraint is never anonymous.
            fn = self._translate_constraint_block(
                ctx, decl,
                ir.DataTypeStruct(name=f"{namespace_prefix}{name}", super=None))
        if fn is None:
            return
        ctx.generic_constraints[f"{namespace_prefix}{name}"] = fn

    def _translate_import_proto(self, ctx: AstToIrContext, node) -> None:
        """Capture a package-scope ``import target/solve function`` declaration.

        Recorded on ``ctx.import_functions`` so the SV backend can expose each as
        a method on ``import_api_if`` and route calls through the handle.
        ``getPlat()``: 1 == target (runs on the SUT -> SV task when void),
        2 == solve (solve-time -> SV function).
        """
        proto = node.getProto() if hasattr(node, 'getProto') else None
        if proto is None:
            return
        name_node = proto.getName()
        func_name = name_node.getId() if isinstance(name_node, pss_ast.ExprId) else str(name_node)

        return_type = None
        rt_node = proto.getRtype() if hasattr(proto, 'getRtype') else None
        if rt_node is not None:
            return_type = self._translate_data_type(ctx, rt_node)

        params: List[ir.Arg] = []
        for i in range(proto.numParameters()):
            param = proto.getParameter(i)
            if param is None:
                continue
            pn_node = param.getName()
            pname = pn_node.getId() if isinstance(pn_node, pss_ast.ExprId) else str(pn_node)
            ptype = self._translate_data_type(ctx, param.getType())
            if ptype is not None:
                params.append(ir.Arg(arg=pname, annotation=ptype))

        plat = node.getPlat() if hasattr(node, 'getPlat') else 0
        ir_func = ir.Function(
            name=func_name,
            args=ir.Arguments(args=params),
            returns=return_type,
            is_async=False,
            is_import=True,
            is_target=(int(plat) == 1),
            is_solve=(int(plat) == 2),
        )
        ctx.import_functions.append(ir_func)

    #: ExecBlock kinds that become a named IR function on the enclosing type.
    #: ``is_async`` matters: an action ``body`` can consume time, the solve-time
    #: blocks and the component init/run hooks cannot.
    _EXEC_FUNCS = {
        pss_ast.ExecKind.ExecKind_Body:      ('body',       True),
        pss_ast.ExecKind.ExecKind_PreSolve:  ('pre_solve',  False),
        pss_ast.ExecKind.ExecKind_PostSolve: ('post_solve', False),
        pss_ast.ExecKind.ExecKind_InitDown:  ('init_down',  False),
        pss_ast.ExecKind.ExecKind_InitUp:    ('init_up',    False),
        pss_ast.ExecKind.ExecKind_RunStart:  ('run_start',  False),
        pss_ast.ExecKind.ExecKind_RunEnd:    ('run_end',    False),
    }

    def _translate_type_body(self, ctx: AstToIrContext, children, target_ir,
                             qualified_name: str):
        """Dispatch the children of a component/action body onto ``target_ir``.

        Shared by ``_translate_component`` (the initial declaration) and
        ``_translate_extend`` (a later ``extend`` of the same type) **so the two
        cannot diverge**. They did diverge, silently, and the cost was the whole
        operation model: ``_translate_extend`` handled only ``Field``,
        ``ExecBlock`` and ``ConstraintBlock``, so every ``target function`` and
        every ``action`` declared in an ``extend component`` — which is where a
        model following the PSS coding guidelines puts all of them — was dropped
        without a word. Translation succeeded, the type map was populated, and
        the generated API was empty. See the operation-model export design, §3A.

        ``qualified_name`` is the name nested actions are parented to: the
        declaring component for an initial declaration, the *extended* type for
        an ``extend``.
        """
        for child in children:
            if child is None:
                continue

            if isinstance(child, pss_ast.Field):
                field = self._translate_field(ctx, child)
                if field:
                    target_ir.fields.append(field)
            elif isinstance(child, pss_ast.FunctionDefinition):
                func = self._translate_function(ctx, child)
                if func:
                    target_ir.functions.append(func)
            elif isinstance(child, pss_ast.Action):
                # Nested action -- registered under its qualified name, with the
                # enclosing component recorded as its parent.
                self._translate_action(ctx, child, parent_comp_name=qualified_name)
            elif isinstance(child, pss_ast.Struct):
                self._translate_struct(ctx, child)
            elif isinstance(child, pss_ast.EnumDecl):
                self._translate_enum(ctx, child)
            elif isinstance(child, pss_ast.ExecBlock):
                entry = self._EXEC_FUNCS.get(child.getKind())
                if entry is not None:
                    name, is_async = entry
                    stmts = self._translate_exec_scope(ctx, child)
                    # `exec_kind` says what this function IS. The name alone
                    # says it too (an exec kind is a keyword, so no declared
                    # function can take it), but a consumer should not have to
                    # know that to tell an exec block from an operation.
                    target_ir.functions.append(
                        ir.Function(name=name, is_async=is_async, body=stmts,
                                    metadata={"exec_kind": name}))
                elif self.debug:
                    self.logger.debug(
                        f"{qualified_name}: unhandled exec kind {child.getKind()}")
            elif isinstance(child, pss_ast.ConstraintBlock):
                constraint_func = self._translate_constraint_block(ctx, child, target_ir)
                if constraint_func:
                    target_ir.functions.append(constraint_func)
            elif isinstance(child, pss_ast.GenericConstraintDeclValue):
                value_func = self._translate_generic_value_constraint(ctx, child)
                if value_func:
                    target_ir.functions.append(value_func)
            elif self.debug:
                # The silent-drop class this method exists to prevent. Anything
                # reaching here is a body element no backend will ever see.
                self.logger.debug(
                    f"{qualified_name}: unhandled body element "
                    f"{type(child).__name__}")

    def _translate_extend(self, ctx: AstToIrContext, extend: pss_ast.ExtendType):
        """Translate a PSS extend declaration, adding fields/functions to the target IR type.

        PSS ``extend action C::a { rand int y; exec body {...} }`` adds new
        fields and exec blocks to the already-translated ``C::a`` DataTypeClass.
        """
        # Every `return` below discards user input, so each one is an error.
        # This method runs in the EXTEND pass, after every declaration has been
        # registered, so a target that is still missing is genuinely missing --
        # it is no longer the "declared in a later file" case that used to reach
        # here and return silently.
        target_ti = extend.getTarget()
        if target_ti is None:
            ctx.add_error("extend: no target type named")
            return

        # Build the qualified name from TypeIdentifier elements
        parts = []
        for i in range(target_ti.numElems()):
            elem = target_ti.getElem(i)
            if elem is not None:
                id_node = elem.getId()
                if id_node is not None and hasattr(id_node, 'getId'):
                    parts.append(id_node.getId())

        if not parts:
            ctx.add_error("extend: target type name could not be read")
            return

        # Look up the target IR type (try both qualified and short names)
        target_name = "::".join(parts)
        target_ir = ctx.type_map.get(target_name)
        if target_ir is None and len(parts) > 1:
            # Try without first part (component prefix): C::a → a
            target_ir = ctx.type_map.get("::".join(parts[1:]))

        if target_ir is None:
            ctx.add_error(f"extend of unknown type '{target_name}'")
            return

        if self.debug:
            self.logger.debug(f"Translating extend for: {target_name}")

        ctx.push_scope(target_ir)
        # The extended type's own name, so a nested action declared here is
        # parented to the type being extended rather than to nothing.
        self._type_chain_stack.append(parts[-1])

        self._translate_type_body(
            ctx,
            (extend.getChild(i) for i in range(extend.numChildren())),
            target_ir,
            getattr(target_ir, "name", None) or target_name)

        self._type_chain_stack.pop()

        # Flush any `rand int in [range]` domain constraints onto the extended type.
        self._flush_range_constraints(target_ir)

        ctx.pop_scope()

    def _translate_extend_enum(self, ctx: AstToIrContext, extend: pss_ast.ExtendEnum):
        """Translate a PSS extend enum, appending new items to the existing IR DataTypeEnum."""
        # As in _translate_extend: this runs after every declaration is
        # registered, so each early return is a real error, not a "not yet".
        target_ti = extend.getTarget()
        if target_ti is None:
            ctx.add_error("extend enum: no target type named")
            return

        parts = []
        for i in range(target_ti.numElems()):
            elem = target_ti.getElem(i)
            if elem is not None:
                id_node = elem.getId()
                if id_node is not None and hasattr(id_node, 'getId'):
                    parts.append(id_node.getId())

        if not parts:
            ctx.add_error("extend enum: target type name could not be read")
            return

        target_name = "::".join(parts)
        target_ir = ctx.type_map.get(target_name)
        if target_ir is None:
            ctx.add_error(f"extend enum of unknown type '{target_name}'")
            return

        if not isinstance(target_ir, ir.DataTypeEnum):
            ctx.add_error(
                f"extend enum of '{target_name}', which is not an enum type")
            return

        next_val = max(target_ir.items.values(), default=-1) + 1
        for i in range(extend.numItems()):
            item = extend.getItem(i)
            if item is None:
                continue
            item_name_node = item.getName()
            item_name = item_name_node.getId() if hasattr(item_name_node, 'getId') else str(item_name_node)
            val_node = item.getValue() if hasattr(item, 'getValue') else None
            if val_node is not None and hasattr(val_node, 'getValue'):
                next_val = val_node.getValue()
            target_ir.items[item_name] = next_val
            next_val += 1

    def _record_scope_function(self, ctx: AstToIrContext, fdef,
                               namespace_prefix: str = "") -> None:
        """Record a package/global-scope function definition on ``ctx.functions``."""
        func = self._translate_function(ctx, fdef)
        if func is None:
            return
        qname = f"{namespace_prefix}{func.name}"
        func.metadata["qualified_name"] = qname
        ctx.functions[qname] = func
        ctx.functions.setdefault(func.name, func)

    def _translate_package(self, ctx: AstToIrContext, pkg: pss_ast.PackageScope,
                           parent_prefix: str = "", phase: '_Phase' = None):
        """Translate a PSS package declaration.

        Types inside the package are registered with a namespace prefix:
        ``package my_pkg { component C {} }`` → type key ``my_pkg::C``.
        """
        parts = [pkg.getId(i).getId() for i in range(pkg.numId())]
        pkg_name = "::".join(parts)
        prefix = f"{parent_prefix}{pkg_name}::" if parent_prefix else f"{pkg_name}::"
        if self.debug:
            self.logger.debug(f"Translating package: {prefix}")
        # Recurse into package children using the same unit-level dispatch
        self._translate_unit(ctx, pkg, namespace_prefix=prefix, phase=phase)

    def _translate_component(self, ctx: AstToIrContext, component: pss_ast.Component, namespace_prefix: str = "") -> ir.DataTypeComponent:
        """Translate a PSS component to IR

        Args:
            ctx: Translation context
            component: PSS component AST node

        Returns:
            IR DataTypeComponent
        """
        self._record_template_params(ctx, component)

        # Extract component name
        name_node = component.getName()
        if isinstance(name_node, pss_ast.ExprId):
            comp_name = name_node.getId()
        else:
            comp_name = str(name_node)

        qualified_name = f"{namespace_prefix}{comp_name}"

        if self.debug:
            self.logger.debug(f"Translating component: {qualified_name}")

        # What does this component derive from? Three answers matter:
        #   `reg_c<R,ACC,SZ>`  -> it IS a register type (handled below)
        #   `reg_group_c`      -> DataTypeRegisterGroup, directly or transitively
        #   anything else      -> DataTypeComponent
        super_name, super_elem = self._super_of(component)
        is_register_group = self._derives_from_reg_group(ctx, super_name)

        # A named register type: `pure component wb_dma_gcsr_r : reg_c<S, RW, 32> {}`.
        #
        # The template arguments are the whole content of such a declaration --
        # value type, access mode and width -- and until this existed they were
        # discarded: the super was recorded as the bare name `reg_c` and every
        # backend saw an ordinary empty component. Registers declared this way
        # silently became misclassified components, which is how a generated
        # package ended up with `interface class wb_dma_gcsr_r_if` and an empty
        # register group. See the operation-model export design §3B / §4.2.
        #
        # The parsing is not re-implemented here: `_translate_reg_c` already
        # extracts R/ACC/SZ and builds the register's accessors and fields. It
        # was simply unreachable from this path -- only from the *field*-type
        # path in `_translate_data_type`.
        if super_name == "reg_c" and super_elem is not None:
            reg = self._translate_reg_c(ctx, super_elem)
            reg.name = qualified_name
            ctx.add_type(qualified_name, reg)
            if namespace_prefix:
                ctx.add_type(comp_name, reg)
            if self.debug:
                self.logger.debug(
                    f"named register type {qualified_name}: "
                    f"{reg.register_value_type}, {reg.access_mode}, {reg.size_bits} bits")
            return reg

        # Create appropriate IR component type
        if is_register_group:
            comp = ir.DataTypeRegisterGroup(name=qualified_name, super=None)
        else:
            comp = ir.DataTypeComponent(name=qualified_name, super=None)
        comp.doc = ast_doc(component)

        # Register in type map (both short and qualified names)
        ctx.add_type(qualified_name, comp)
        if namespace_prefix:
            ctx.add_type(comp_name, comp)

        # Push scope and type-chain name for annotation matching
        ctx.push_scope(comp)
        self._type_chain_stack.append(comp_name)

        # Set super type reference if present -- named as the LINKER resolved
        # it (`p::base_c`, not the last element of how it was spelled), so a
        # consumer finds the base without guessing. A template base
        # (`executor_c<>`, `reg_c<...>`) keeps its spelled name.
        if super_name:
            comp.super = ir.DataTypeRef(
                ref_name=self._linked_type_name(ctx, component.getSuper_t())
                or super_name)

        # Translate children (fields, functions, nested types). Shared with
        # `extend component <this>` -- see _translate_type_body.
        self._translate_type_body(ctx, component.children(), comp, qualified_name)

        # Flush any `rand int in [range]` domain constraints onto this component.
        self._flush_range_constraints(comp)

        # Pop type-chain name for component
        self._type_chain_stack.pop()

        # Add built-in functions for register groups
        if is_register_group:
            self._add_register_group_functions(ctx, comp)
            # Compute register offsets
            self._compute_register_offsets(ctx, comp)

        # Consume explicit `pool [N] T name;` declarations (FieldPool AST nodes)
        # so real pool names and capacities reach the IR.
        self._translate_declared_pools(ctx, component, comp)

        # Consume explicit `bind pool targets;` directives (ComponentBind AST
        # nodes) so real pool binds reach the IR.
        self._translate_component_binds(ctx, component, comp)

        # Pop scope
        ctx.pop_scope()

        return comp

    def _fold_const_expr(self, ctx: AstToIrContext, expr) -> Optional[int]:
        """Fold ``expr`` to an int if it names a known package-scope constant.

        Deliberately shallow: a bare identifier or a qualified static path,
        nothing arithmetic. Anything else returns None and the caller reports
        "unknown" rather than a guess.
        """
        if expr is None:
            return None
        if isinstance(expr, pss_ast.ExprId):
            return ctx.const_map.get(expr.getId())
        # A bare identifier in template-argument position is parsed as a TYPE
        # argument -- `array<S, N>` cannot be disambiguated without knowing what
        # N is. So the constant arrives here spelled as a type reference.
        if isinstance(expr, pss_ast.DataTypeUserDefined):
            return self._fold_const_expr(ctx, expr.getType_id())
        if isinstance(expr, pss_ast.TypeIdentifier) and expr.numElems() > 0:
            parts = []
            for i in range(expr.numElems()):
                eid = expr.getElem(i).getId()
                if hasattr(eid, 'getId'):
                    parts.append(eid.getId())
            if parts:
                return (ctx.const_map.get("::".join(parts))
                        or ctx.const_map.get(parts[-1]))
        # pkg::NAME
        if isinstance(expr, pss_ast.ExprRefPathStatic) and expr.numBase() > 0:
            parts = []
            for i in range(expr.numBase()):
                elem = expr.getBase(i)
                eid = elem.getId() if hasattr(elem, 'getId') else None
                if eid is not None and hasattr(eid, 'getId'):
                    parts.append(eid.getId())
            if parts:
                return (ctx.const_map.get("::".join(parts))
                        or ctx.const_map.get(parts[-1]))
        # NAME, as an unqualified reference: ExprRefPathContext -> hier id.
        hier = getattr(expr, 'getHier_id', lambda: None)()
        if hier is not None and getattr(hier, 'numElems', lambda: 0)() > 0:
            parts = []
            for i in range(hier.numElems()):
                eid = hier.getElem(i).getId()
                if hasattr(eid, 'getId'):
                    parts.append(eid.getId())
            if parts:
                return (ctx.const_map.get("::".join(parts))
                        or ctx.const_map.get(parts[-1]))
        leaf = getattr(expr, 'getLeaf', lambda: None)()
        if leaf is not None and hasattr(leaf, 'getId'):
            return ctx.const_map.get(leaf.getId())
        return None

    @staticmethod
    def _super_of(component) -> Tuple[Optional[str], Optional[object]]:
        """``(super type name, its TypeIdentifierElem)`` for a component.

        The elem is returned as well as the name because it is what carries the
        template arguments -- ``reg_c<R, ACC, SZ>`` is a *specialization*, and
        keeping only ``"reg_c"`` throws away everything that makes one register
        type different from another. The LAST element is the type: a qualified
        super reads ``pkg::reg_c<...>``.
        """
        super_t = component.getSuper_t()
        if super_t is None:
            return None, None
        if isinstance(super_t, pss_ast.ExprId):
            return super_t.getId(), None
        if isinstance(super_t, pss_ast.TypeIdentifier):
            if super_t.numElems() == 0:
                return None, None
            elem = super_t.getElem(super_t.numElems() - 1)
            elem_id = elem.getId()
            name = elem_id.getId() if isinstance(elem_id, pss_ast.ExprId) else str(elem_id)
            return name, elem
        return str(super_t), None

    def _derives_from_reg_group(self, ctx: AstToIrContext, super_name: Optional[str]) -> bool:
        """Is ``super_name`` ``reg_group_c``, or something derived from it?

        Resolved **transitively** through the type map, not by matching the
        immediate super's spelling. A group one level further derived -- a
        project base class over ``reg_group_c``, say -- is still a register
        group, and treating it as an ordinary component drops its entire
        address map without complaint.
        """
        seen = set()
        while super_name and super_name not in seen:
            if super_name in ("reg_group_c", "addr_reg_pkg::reg_group_c"):
                return True
            seen.add(super_name)
            resolved = ctx.get_type(super_name)
            if isinstance(resolved, ir.DataTypeRegisterGroup):
                return True
            sup = getattr(resolved, "super", None) if resolved is not None else None
            super_name = getattr(sup, "ref_name", None)
        return False

    def _translate_declared_pools(self, ctx: AstToIrContext, component, comp: ir.DataTypeComponent):
        """Create IR ``Pool``s from explicit ``pool [N] T name;`` declarations.

        These are ``FieldPool`` AST nodes (surfaced by the parser as of the
        pssparser-detox B1 change).  Each yields a real pool carrying the
        source-declared name and capacity, so pool sizes reach the IR instead of
        being inferred with a fixed default.
        """
        for child in component.children():
            if not isinstance(child, pss_ast.FieldPool):
                continue
            name_node = child.getName()
            pool_name = (name_node.getId()
                         if isinstance(name_node, pss_ast.ExprId) else str(name_node))
            elem_type = self._translate_data_type(ctx, child.getType())
            elem_type_name = elem_type.name if isinstance(elem_type, ir.DataTypeStruct) \
                else self._pool_elem_type_name(child.getType())
            pool = ir.Pool(
                name=pool_name,
                element_type_name=elem_type_name,
                element_type=elem_type if isinstance(elem_type, ir.DataTypeStruct) else None,
                capacity=self._eval_pool_size(child.getSize()),
            )
            comp.pools.append(pool)

    def _translate_component_binds(self, ctx: AstToIrContext, component, comp: ir.DataTypeComponent):
        """Create IR ``PoolBind``s from explicit ``bind pool targets;`` directives.

        These are ``ComponentBind`` AST nodes (surfaced by the parser as of the
        pssparser-detox B1b change).  Each yields a real ``PoolBind`` carrying
        the bound pool name, the wildcard flag, and any explicit dotted target
        paths, so real binds reach the IR instead of being inferred.
        """
        for child in component.children():
            if not isinstance(child, pss_ast.ComponentBind):
                continue
            pool_path = child.getPool_path()
            # The pool is named by the final element of the (usually trivial)
            # hierarchical path, matching declared pool names.
            pool_name = pool_path.split(".")[-1] if pool_path else pool_path
            comp.pool_binds.append(ir.PoolBind(
                pool_name=pool_name,
                field_paths=[
                    p for p in (self._bind_target_path(t) for t in child.getTargets())
                    if p is not None
                ],
                is_wildcard=child.getIs_wildcard(),
            ))

    @staticmethod
    def _bind_target_path(target) -> Optional[str]:
        """Flatten a ``ComponentBindTarget`` to its dotted path, or ``None``.

        The parser splits ``producer.out`` across two accessors: everything up to
        the last dot arrives as ``getType_id()`` (it is resolved as a type
        reference), and the trailing member as ``getField()``.  A wildcard target
        (``bind dpool *;``) names no path at all -- the wildcard is already
        recorded on the enclosing ``PoolBind``.
        """
        if target.getIs_wildcard():
            return None
        parts: List[str] = []
        type_id = target.getType_id()
        if type_id is not None:
            for i in range(type_id.numElems()):
                elem_id = type_id.getElem(i).getId()
                if elem_id is not None:
                    parts.append(elem_id.getId())
        field = target.getField()
        if field is not None:
            parts.append(field.getId())
        return ".".join(parts) if parts else None

    def _pool_elem_type_name(self, type_node) -> Optional[str]:
        """Best-effort element-type name from a pool's DataTypeUserDefined node."""
        if not isinstance(type_node, pss_ast.DataTypeUserDefined):
            return None
        type_id = type_node.getType_id()
        if isinstance(type_id, pss_ast.TypeIdentifier):
            if type_id.numElems() == 0:
                return None
            parts = []
            for i in range(type_id.numElems()):
                e_id = type_id.getElem(i).getId()
                parts.append(e_id.getId() if isinstance(e_id, pss_ast.ExprId) else str(e_id))
            return "::".join(parts)
        if isinstance(type_id, pss_ast.ExprId):
            return type_id.getId()
        return str(type_id) if type_id is not None else None

    def _eval_pool_size(self, size_node) -> Optional[int]:
        """Evaluate a pool size expression to an int, or None if unsized/unknown."""
        if size_node is None:
            return None
        get_val = getattr(size_node, "getValue", None)
        if get_val is not None:
            try:
                return int(get_val())
            except (TypeError, ValueError):
                return None
        return None

    def _translate_action(self, ctx: AstToIrContext, action: pss_ast.Action,
                          parent_comp_name: Optional[str] = None,
                          namespace_prefix: str = "") -> ir.DataTypeClass:
        """Translate a PSS action to IR DataTypeClass
        
        Args:
            ctx: Translation context
            action: PSS action AST node
            parent_comp_name: Name of enclosing component, if any
            namespace_prefix: Namespace prefix for package-scoped types
            
        Returns:
            IR DataTypeClass
        """
        self._record_template_params(ctx, action)

        # Extract action name
        name_node = action.getName()
        if isinstance(name_node, pss_ast.ExprId):
            action_name = name_node.getId()
        else:
            action_name = str(name_node)
            
        if self.debug:
            self.logger.debug(f"Translating action: {action_name}")
            
        # Create IR class for action
        action_ir = ir.DataTypeClass(name=action_name, super=None)
        
        # Register under qualified name if nested in a component, simple name otherwise
        if parent_comp_name:
            qname = f"{parent_comp_name}::{action_name}"
            ctx.add_type(qname, action_ir)
            ctx.parent_comp_names[qname] = parent_comp_name
        elif namespace_prefix:
            qname = f"{namespace_prefix}{action_name}"
            action_ir.name = qname
            ctx.add_type(qname, action_ir)
            ctx.add_type(action_name, action_ir)
        else:
            ctx.add_type(action_name, action_ir)
        
        # Push scope and type-chain name for annotation matching
        ctx.push_scope(action_ir)
        self._type_chain_stack.append(action_name)

        # Handle inheritance. The base is named as the LINKER resolved it: an
        # action's base may be written `A` for `pss_top::A`, or be found in a
        # base component (`B` for `base_c::B`), which no lookup of the spelling
        # in the type table gets right.
        super_t = action.getSuper_t()
        if super_t is not None:
            super_name = (self._linked_type_name(ctx, super_t)
                          or self._type_identifier_name(super_t))
            if super_name:
                action_ir.super = ir.DataTypeRef(ref_name=super_name)

        # Handle abstract flag
        if hasattr(action, 'getIs_abstract') and action.getIs_abstract():
            action_ir.is_abstract = True
            
        # Translate children (fields, exec blocks, and constraints)
        for child in action.children():
            if child is None:
                continue
                
            if isinstance(child, pss_ast.Field):
                field = self._translate_field(ctx, child)
                if field:
                    action_ir.fields.append(field)
            elif isinstance(child, pss_ast.FieldRef):
                field = self._translate_field_ref(ctx, child)
                if field:
                    action_ir.fields.append(field)
            elif isinstance(child, pss_ast.ActionHandleField):
                # Named action handle declared at the action or activity level.
                # E.g. `link_init a_init;` → Field(kind=Field, name='a_init',
                #       datatype=DataTypeRef('link_init'))
                # These become class-level handles on the action, constructed
                # in pre_solve() before randomize().
                name_node = child.getName()
                handle_name = name_node.getId() if hasattr(name_node, 'getId') else str(name_node)
                type_node = child.getType()
                type_id = type_node.getType_id() if hasattr(type_node, 'getType_id') else None
                type_parts: list = []
                if type_id:
                    for _ti in range(type_id.numElems()):
                        elem = type_id.getElem(_ti)
                        eid = elem.getId() if hasattr(elem, 'getId') else None
                        if eid and hasattr(eid, 'getId'):
                            type_parts.append(eid.getId())
                handle_type_name = '::'.join(type_parts) if type_parts else None
                if handle_name and handle_type_name:
                    from zuspec.ir.core.fields import FieldKind as _HFK
                    _hf = ir.Field(
                        name=handle_name,
                        kind=_HFK.Field,
                        datatype=ir.DataTypeRef(ref_name=handle_type_name),
                    )
                    action_ir.fields.append(_hf)
            elif isinstance(child, pss_ast.FieldClaim):
                # lock/share resource claim (PSS LRM section 9.3)
                field = self._translate_field_claim(ctx, child)
                if field:
                    action_ir.fields.append(field)
            elif isinstance(child, pss_ast.ExecBlock):
                kind = child.getKind()
                if kind == pss_ast.ExecKind.ExecKind_Body:
                    stmts = self._translate_exec_scope(ctx, child)
                    func = ir.Function(name='body', is_async=True, body=stmts)
                    action_ir.functions.append(func)
                elif kind == pss_ast.ExecKind.ExecKind_PreSolve:
                    stmts = self._translate_exec_scope(ctx, child)
                    func = ir.Function(name='pre_solve', is_async=False, body=stmts)
                    action_ir.functions.append(func)
                elif kind == pss_ast.ExecKind.ExecKind_PostSolve:
                    stmts = self._translate_exec_scope(ctx, child)
                    func = ir.Function(name='post_solve', is_async=False, body=stmts)
                    action_ir.functions.append(func)
            elif isinstance(child, pss_ast.ConstraintBlock):
                constraint_func = self._translate_constraint_block(ctx, child, action_ir)
                if constraint_func:
                    action_ir.functions.append(constraint_func)
            elif isinstance(child, pss_ast.GenericConstraintDeclValue):
                value_func = self._translate_generic_value_constraint(ctx, child)
                if value_func:
                    action_ir.functions.append(value_func)
            elif isinstance(child, pss_ast.Covergroup):
                cg = self._translate_covergroup(ctx, child)
                if cg is not None:
                    action_ir.covergroups.append(cg)
            elif isinstance(child, pss_ast.ActivityDecl):
                action_ir.activity_ir = self._translate_activity_body(ctx, child)

        # Flush any `rand int in [range]` domain constraints onto this action.
        self._flush_range_constraints(action_ir)

        # Pop scope and type-chain name
        self._type_chain_stack.pop()
        ctx.pop_scope()
        
        return action_ir

    # ------------------------------------------------------------------
    # Activity body translation
    # ------------------------------------------------------------------

    def _translate_activity_body(
        self,
        ctx: 'AstToIrContext',
        activity_decl: 'pss_ast.ActivityDecl',
    ) -> 'ir.ActivitySequenceBlock':
        """Translate a PSS ActivityDecl to an IR ActivitySequenceBlock."""
        stmts = self._translate_activity_stmts(ctx, activity_decl.children())
        return ir.ActivitySequenceBlock(stmts=stmts)

    def _translate_activity_stmts(
        self,
        ctx: 'AstToIrContext',
        children,
    ) -> List['ir.ActivityStmt']:
        """Translate an iterable of PSS activity child nodes to IR ActivityStmt list."""
        result: List[ir.ActivityStmt] = []
        for child in children:
            if child is None:
                continue
            stmt = self._translate_activity_stmt(ctx, child)
            if stmt is not None:
                result.append(stmt)
        return result

    def _translate_activity_stmt(
        self,
        ctx: 'AstToIrContext',
        node,
    ) -> Optional['ir.ActivityStmt']:
        """Translate a single PSS activity AST node to an IR ActivityStmt."""

        if isinstance(node, (pss_ast.ActivitySequence, pss_ast.ActivityDecl)):
            stmts = self._translate_activity_stmts(ctx, node.children())
            return ir.ActivitySequenceBlock(stmts=stmts)

        if isinstance(node, pss_ast.ActivityParallel):
            stmts = self._translate_activity_stmts(ctx, node.children())
            join_spec = self._translate_join_spec(node.getJoin_spec())
            return ir.ActivityParallel(stmts=stmts, join_spec=join_spec)

        if isinstance(node, pss_ast.ActivitySchedule):
            stmts = self._translate_activity_stmts(ctx, node.children())
            return ir.ActivitySchedule(stmts=stmts)

        if isinstance(node, pss_ast.ActivityAtomicBlock):
            stmts = self._translate_activity_stmts(ctx, node.children())
            return ir.ActivityAtomic(stmts=stmts)

        if isinstance(node, pss_ast.ActivityActionHandleTraversal):
            return self._translate_handle_traversal(ctx, node)

        if isinstance(node, pss_ast.ActivityActionTypeTraversal):
            return self._translate_type_traversal(ctx, node)

        if isinstance(node, pss_ast.ActivitySuper):
            return ir.ActivitySuper()

        if isinstance(node, pss_ast.ActivityRepeatCount):
            count_expr = self._translate_expression(ctx, node.getCount())
            loop_var = node.getLoop_var()
            index_var = loop_var.getId() if loop_var and hasattr(loop_var, 'getId') else None
            body_stmts = self._translate_activity_stmts(ctx, _activity_body_children(node.getBody()))
            return ir.ActivityRepeat(count=count_expr, index_var=index_var, body=body_stmts)

        if isinstance(node, pss_ast.ActivityRepeatWhile):
            cond_expr = self._translate_expression(ctx, node.getCond())
            body_stmts = self._translate_activity_stmts(ctx, _activity_body_children(node.getBody()))
            return ir.ActivityDoWhile(condition=cond_expr, body=body_stmts)

        if isinstance(node, pss_ast.ActivityForeach):
            it_id = node.getIt_id()
            iterator = it_id.getId() if it_id else '_item'
            idx_id = node.getIdx_id()
            index_var = idx_id.getId() if idx_id else None
            target_expr = self._translate_expression(ctx, node.getTarget())
            body_stmts = self._translate_activity_stmts(ctx, _activity_body_children(node.getBody()))
            return ir.ActivityForeach(
                iterator=iterator, collection=target_expr,
                index_var=index_var, body=body_stmts,
            )

        if isinstance(node, pss_ast.ActivityIfElse):
            cond_expr = self._translate_expression(ctx, node.getCond())
            true_s = node.getTrue_s()
            false_s = node.getFalse_s()
            if_body: List[ir.ActivityStmt] = []
            else_body: List[ir.ActivityStmt] = []
            if true_s:
                s = self._translate_activity_stmt(ctx, true_s)
                if s:
                    if hasattr(s, 'stmts'):
                        if_body.extend(s.stmts)
                    else:
                        if_body.append(s)
            if false_s:
                s = self._translate_activity_stmt(ctx, false_s)
                if s:
                    if hasattr(s, 'stmts'):
                        else_body.extend(s.stmts)
                    else:
                        else_body.append(s)
            return ir.ActivityIfElse(condition=cond_expr, if_body=if_body, else_body=else_body)

        if isinstance(node, pss_ast.ActivitySelect):
            branches: List[ir.SelectBranch] = []
            for bi in range(node.numBranches()):
                b = node.getBranche(bi)
                if b is None:
                    continue
                guard = self._translate_expression(ctx, b.getGuard()) if b.getGuard() else None
                weight = self._translate_expression(ctx, b.getWeight()) if b.getWeight() else None
                body = b.getBody()
                body_stmts: List[ir.ActivityStmt] = []
                if body:
                    s = self._translate_activity_stmt(ctx, body)
                    if s:
                        if hasattr(s, 'stmts'):
                            body_stmts.extend(s.stmts)
                        else:
                            body_stmts.append(s)
                branches.append(ir.SelectBranch(guard=guard, weight=weight, body=body_stmts))
            return ir.ActivitySelect(branches=branches)

        if isinstance(node, pss_ast.ActivityReplicate):
            count_expr = self._translate_expression(ctx, node.getCount())
            # ``replicate (i : count)`` -- the optional index identifier.  Unlike
            # ActivityRepeatCount (which still spells it ``getLoop_var``),
            # ActivityReplicate names this accessor ``getIdx_id``.
            idx_id = node.getIdx_id()
            index_var = idx_id.getId() if idx_id is not None and hasattr(idx_id, 'getId') else None
            body_stmts = self._translate_activity_stmts(ctx, _activity_body_children(node.getBody()))
            return ir.ActivityReplicate(count=count_expr, index_var=index_var, body=body_stmts)

        if isinstance(node, pss_ast.ActivityMatch):
            cond_expr = self._translate_expression(ctx, node.getCond())
            cases: List[ir.MatchCase] = []
            for ci in range(node.numChoices()):
                choice = node.getChoice(ci)
                is_default = bool(choice.getIs_default())
                if is_default:
                    pattern = None
                else:
                    # Translate ExprOpenRangeList -> ExprRangeList
                    pattern = self._translate_open_range_list(ctx, choice.getCond())
                body = choice.getBody()
                body_stmts = self._translate_activity_stmts(
                    ctx, _activity_body_children(body))
                cases.append(ir.MatchCase(pattern=pattern, body=body_stmts))
            return ir.ActivityMatch(subject=cond_expr, cases=cases)

        if isinstance(node, pss_ast.ActivityConstraint):
            exprs: List[ir.Expr] = []
            c = node.getConstraint()
            if c is not None:
                stmts: List[ir.Stmt] = []
                self._collect_constraint_stmt(ctx, c, stmts)
                for s in stmts:
                    if isinstance(s, ir.StmtExpr):
                        exprs.append(s.expr)
            return ir.ActivityConstraint(constraints=exprs)

        if isinstance(node, pss_ast.ActivityBindStmt):
            # Translate the LHS (ExprHierarchicalId -> ExprAttribute chain)
            lhs_expr = self._hier_id_to_expr(node.getLhs())
            # Translate each RHS; emit one ActivityBind per RHS item
            if node.numRhs() == 0:
                return None
            if node.numRhs() == 1:
                rhs_expr = self._hier_id_to_expr(node.getRh(0))
                return ir.ActivityBind(src=lhs_expr, dst=rhs_expr)
            # Multiple RHS: wrap in a sequence block
            binds = []
            for i in range(node.numRhs()):
                rhs_expr = self._hier_id_to_expr(node.getRh(i))
                binds.append(ir.ActivityBind(src=lhs_expr, dst=rhs_expr))
            return ir.ActivitySequenceBlock(stmts=binds)

        if self.debug:
            self.logger.debug(f"Unhandled activity stmt type: {type(node).__name__}")
        return None

    def _translate_handle_traversal(
        self,
        ctx: 'AstToIrContext',
        node: 'pss_ast.ActivityActionHandleTraversal',
    ) -> Optional['ir.ActivityTraversal']:
        """Extract handle name from ExprRefPathContext and build ActivityTraversal."""
        target = node.getTarget()
        hier_id = target.getHier_id()
        parts: List[str] = []
        for i in range(hier_id.numElems()):
            elem = hier_id.getElem(i)
            if elem:
                id_obj = elem.getId()
                if id_obj and hasattr(id_obj, 'getId'):
                    parts.append(id_obj.getId())
        handle = '.'.join(parts) if parts else None
        if handle is None:
            return None
        inline_constraints = self._extract_inline_constraints(ctx, node)
        return ir.ActivityTraversal(handle=handle, inline_constraints=inline_constraints)

    def _translate_type_traversal(
        self,
        ctx: 'AstToIrContext',
        node: 'pss_ast.ActivityActionTypeTraversal',
    ) -> 'ir.ActivityAnonTraversal':
        """Extract action type name and optional label; build ActivityAnonTraversal."""
        target = node.getTarget()
        type_id = target.getType_id()
        parts: List[str] = []
        for i in range(type_id.numElems()):
            elem = type_id.getElem(i)
            if elem:
                id_obj = elem.getId()
                if hasattr(id_obj, 'getId'):
                    parts.append(id_obj.getId())
                elif hasattr(elem, 'getId'):
                    raw = elem.getId()
                    if hasattr(raw, 'getId'):
                        parts.append(raw.getId())
        action_type = '::'.join(parts) if parts else ''

        label = None
        label_node = node.getLabel()
        if label_node:
            label = label_node.getId() if hasattr(label_node, 'getId') else str(label_node)

        inline_constraints = self._extract_inline_constraints(ctx, node)
        # WI-6: detect and strip ``comp == expr`` from inline constraints.
        # PSS allows ``do T with comp == target;`` to route the traversal to a
        # specific component instance.  Extract it as comp_expr so the activity
        # runner can pass it as a comp_override to _traverse.
        comp_expr, filtered = self._extract_comp_expr(inline_constraints)
        return ir.ActivityAnonTraversal(
            action_type=action_type,
            label=label,
            inline_constraints=filtered,
            comp_expr=comp_expr,
        )

    def _extract_inline_constraints(self, ctx, node) -> list:
        """Extract `with` constraint expressions from a traversal node.

        PSS supports two forms:
          block:  do T with { expr1; expr2; }   -> ConstraintScope with numConstraints()
          single: do T with expr;               -> ConstraintStmtExpr with getExpr()
        """
        with_c = node.getWith_c() if hasattr(node, 'getWith_c') else None
        if not with_c:
            return []
        results = []
        # Block form: constraint scope containing multiple items
        if hasattr(with_c, 'numConstraints'):
            for ci in range(with_c.numConstraints()):
                cs = with_c.getConstraint(ci)
                if cs is None:
                    continue
                stmts: list = []
                self._collect_constraint_stmt(ctx, cs, stmts)
                for s in stmts:
                    if isinstance(s, ir.StmtExpr):
                        results.append(s.expr)
        # Single-expression form: do T with expr; -> ConstraintStmtExpr
        elif hasattr(with_c, 'getExpr'):
            expr_node = with_c.getExpr()
            if expr_node is not None:
                expr_ir = self._translate_expression(ctx, expr_node)
                if expr_ir is not None:
                    results.append(expr_ir)
        return results


    def _extract_comp_expr(self, constraints: list):
        """Separate a ``comp == expr`` equality from other inline constraints.

        Returns ``(comp_expr, remaining)`` where *comp_expr* is the RHS of the
        ``comp == <rhs>`` expression (or ``None`` if not present), and
        *remaining* is the list without that item.

        PSS ``do T with comp == target;`` produces an ExprCompare(CmpOp.Eq,
        ExprRefUnresolved("comp"), <rhs>) in the IR.
        """
        from zuspec.ir.core.expr import (
            ExprCompare, CmpOp, ExprBin, BinOp,
            ExprRefUnresolved, ExprAttribute,
        )

        def _is_comp_ref(e):
            return (isinstance(e, ExprRefUnresolved) and e.name == 'comp') or                    (isinstance(e, ExprAttribute) and e.attr == 'comp')

        comp_expr = None
        remaining = []
        for c in constraints:
            matched = False
            # Handle both ExprCompare (CmpOp.Eq) and ExprBin (BinOp.Eq)
            if isinstance(c, ExprCompare) and c.op == CmpOp.Eq:
                if _is_comp_ref(c.left):
                    comp_expr = c.right; matched = True
                elif _is_comp_ref(c.right):
                    comp_expr = c.left; matched = True
            elif isinstance(c, ExprBin) and c.op == BinOp.Eq:
                if _is_comp_ref(c.lhs):
                    comp_expr = c.rhs; matched = True
                elif _is_comp_ref(c.rhs):
                    comp_expr = c.lhs; matched = True
            if not matched:
                remaining.append(c)
        return comp_expr, remaining

    def _translate_open_range_list(self, ctx, range_list_node) -> 'ir.ExprRangeList':
        """Translate a PSS ExprOpenRangeList to an IR ExprRangeList."""
        ranges = []
        if range_list_node is None:
            return ir.ExprRangeList(ranges=[])
        for vi in range(range_list_node.numValues()):
            val = range_list_node.getValue(vi)
            lhs = val.getLhs()
            rhs = val.getRhs()
            lower = self._translate_expression(ctx, lhs) if lhs else None
            upper = self._translate_expression(ctx, rhs) if rhs else None
            if lower is not None:
                ranges.append(ir.ExprRange(lower=lower, upper=upper))
        return ir.ExprRangeList(ranges=ranges)

    def _translate_domain_range_list(self, ctx, domain_node) -> 'Optional[ir.ExprRangeList]':
        """Translate a PSS ExprDomainOpenRangeList to an IR ExprRangeList.

        Used for `rand int in [range]` domain constraint generation.
        """
        if domain_node is None:
            return None
        ranges = []
        for vi in range(domain_node.numValues()):
            val = domain_node.getValue(vi)
            lhs = val.getLhs()
            rhs = val.getRhs()
            lower = self._translate_expression(ctx, lhs) if lhs else None
            upper = self._translate_expression(ctx, rhs) if rhs else None
            if lower is not None:
                ranges.append(ir.ExprRange(lower=lower, upper=upper))
        if not ranges:
            return None
        return ir.ExprRangeList(ranges=ranges)

    def _flush_range_constraints(self, type_ir: ir.DataTypeStruct) -> None:
        """Emit one constraint function for any of ``type_ir``'s own fields that
        carry a ``rand int in [range]`` domain.

        Scoped to ``type_ir.fields`` so a domain on one type can never leak onto
        another (e.g. a std-lib struct's ``alignment`` domain onto a user action).
        """
        pending = [(f, getattr(f, '_pssc_domain_in', None)) for f in type_ir.fields]
        pending = [(f, in_expr) for f, in_expr in pending if in_expr is not None]
        if not pending:
            return
        body: List[ir.Stmt] = [ir.StmtExpr(expr=in_expr) for _f, in_expr in pending]
        n = sum(1 for f in type_ir.functions if f.metadata.get('_is_constraint'))
        cfunc = ir.Function(
            name=f'_range_{n}',
            body=body,
            metadata={'_is_constraint': True},
        )
        type_ir.functions.append(cfunc)
        for f, _in_expr in pending:
            try:
                delattr(f, '_pssc_domain_in')
            except AttributeError:
                pass

    #: How deep a chain of generic constraints may reference one another before
    #: the translator gives up. Recursion is legal (§13.1.2 d) as long as it
    #: terminates; this bound is what turns a non-terminating one into a
    #: diagnostic rather than a stack overflow.
    _MAX_GENERIC_CONSTRAINT_DEPTH = 32

    #: How far up a `super` chain to look for an inherited generic constraint
    #: before assuming the chain is circular.
    _MAX_SUPER_DEPTH = 32

    def _inline_all_generic_constraints(self, ctx: AstToIrContext) -> None:
        """Expand generic constraint references across the whole translated model.

        Each type is visited once. ``ctx.type_map`` registers many types under
        more than one key (bare and qualified), and expanding twice would double
        every instantiated constraint -- harmless for a range, wrong for anything
        counted.

        The key a type is visited under matters: the enclosing component of an
        action is recorded in ``ctx.parent_comp_names`` against its *qualified*
        name, so a type reached by its bare name would not find its component's
        declarations. Hence the ranking below rather than "whichever key came
        first".
        """
        best_key: Dict[int, str] = {}
        for key, dt in ctx.type_map.items():
            if not getattr(dt, 'functions', None):
                continue
            rank = (key in ctx.parent_comp_names, "::" in key)
            prev = best_key.get(id(dt))
            if prev is None or rank > (prev in ctx.parent_comp_names, "::" in prev):
                best_key[id(dt)] = key

        seen: Set[int] = set()
        for key, dt in ctx.type_map.items():
            if id(dt) in seen or best_key.get(id(dt)) != key:
                continue
            seen.add(id(dt))
            self._inline_generic_constraints(ctx, dt, key)

    def _inline_generic_constraints(self, ctx: AstToIrContext,
                                    type_ir: ir.DataTypeStruct,
                                    type_key: Optional[str] = None) -> None:
        """Replace each reference to a generic constraint with its body (§13.1.2).

        A generic constraint is inert until referenced, and a reference
        instantiates the body with the given arguments. Both halves are done here,
        after the whole model is translated, so that a reference may precede the
        declaration it names -- which PSS permits and which a single forward pass
        could not resolve.

        A reference is handled wherever it appears in a boolean context -- as a
        statement of its own, nested in an if/else arm or a ``foreach`` body, or as
        an operand of ``&&``/``||``/``!`` or an implication. A reference to the
        value-yielding form (§13.1.2 b) is handled in any expression position:
        its expression is substituted at the use site, so the reference is typed
        by its own arguments and context rather than once for all references.
        """
        generics = self._visible_generics(ctx, type_ir, type_key)
        if not generics:
            return
        for fn in type_ir.functions:
            if not fn.metadata.get('_is_constraint'):
                continue
            fn.body = self._expand_generic_refs(ctx, fn.body, generics, ())

    def _visible_generics(
        self,
        ctx: AstToIrContext,
        type_ir: ir.DataTypeStruct,
        type_key: Optional[str],
    ) -> Dict[str, '_GenericRef']:
        """Every generic constraint a constraint on *type_ir* may reference.

        §13.1.2 allows a declaration in struct, action and component scope, and in
        package scope where it is always static. The layers below are added
        innermost first and the first entry for a name wins, which is what makes a
        derived declaration shadow a base one (§13.1.2 c).

        Each entry records whether the declaration is *foreign* -- declared in a
        scope whose ``self`` is not this type's. That distinction is not
        cosmetic: expanding a foreign body that touches its own scope's fields
        would rebind those fields to same-named ones on the referencing type, so
        the flag is what lets :meth:`_expand_generic_refs` report it instead.
        """
        out: Dict[str, _GenericRef] = {}

        def add(name: str, fn: ir.Function, foreign: bool, scope: str) -> None:
            if name not in out:
                out[name] = _GenericRef(
                    fn=fn, foreign=foreign, scope=scope,
                    is_value=bool(fn.metadata.get('_is_generic_value')))

        # 1. The type itself, then its base types. `self` is the same object all
        #    the way up, so none of these are foreign. This is also the only layer
        #    where shadowing happens, so it is where §13.1.2 c is checked.
        for dt in self._type_and_bases(ctx, type_ir):
            for f in getattr(dt, 'functions', []) or []:
                if not f.metadata.get('_is_generic_constraint'):
                    continue
                scope = getattr(dt, 'name', '?')
                self._check_shadow_signature(ctx, out.get(f.name), f, scope)
                add(f.name, f, False, scope)

        # 2. The enclosing component of an action. `self` there is the component.
        comp_name = ctx.parent_comp_names.get(type_key) if type_key else None
        comp = self._lookup_type(ctx, comp_name) if comp_name else None
        if comp is not None and comp is not type_ir:
            for f in getattr(comp, 'functions', []) or []:
                if f.metadata.get('_is_generic_constraint'):
                    add(f.name, f, True, comp_name)

        # 3. Package scope, reachable only by qualified name (`p::lt`), so these
        #    cannot shadow anything above and are added unconditionally.
        for qname, f in ctx.generic_constraints.items():
            add(qname, f, True, qname.rsplit("::", 1)[0])

        return out

    def _check_shadow_signature(self, ctx: AstToIrContext,
                                nearer: Optional['_GenericRef'],
                                base_fn: ir.Function, base_scope: str) -> None:
        """§13.1.2 c: a shadowing declaration's return and parameter types must match.

        *nearer* is the declaration already found on a derived type, *base_fn* the
        same name reappearing further up the chain. Only the first such pair is
        reported per name, because the walk stops adding after the nearest entry
        and a three-deep chain of mismatches is one mistake, not two.

        The signatures are compared as recorded text, so the message can show both
        -- a mismatch the user cannot see spelled out is a message they have to
        go and reconstruct by hand.
        """
        if nearer is None or nearer.fn is base_fn:
            return
        derived_sig = nearer.fn.metadata.get('_generic_signature')
        base_sig = base_fn.metadata.get('_generic_signature')
        if derived_sig is None or base_sig is None or derived_sig == base_sig:
            return

        def text(sig) -> str:
            ret, ptypes = sig
            params = ", ".join(ptypes)
            return f"({params}) -> {ret}"

        ctx.errors.append(
            f"generic constraint '{base_fn.name}' in '{nearer.scope}' shadows the "
            f"one in '{base_scope}', but their signatures differ: "
            f"{text(derived_sig)} vs {text(base_sig)}; the return and parameter "
            f"types shall match (PSS 3.1 §13.1.2 c)")

    def _type_and_bases(self, ctx: AstToIrContext, type_ir) -> List[Any]:
        """*type_ir* followed by its base types, nearest first."""
        chain = [type_ir]
        seen = {id(type_ir)}
        current = type_ir
        for _ in range(self._MAX_SUPER_DEPTH):
            super_ref = getattr(current, 'super', None)
            ref_name = getattr(super_ref, 'ref_name', None)
            if not ref_name:
                break
            current = self._lookup_type(ctx, ref_name)
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            chain.append(current)
        return chain

    @staticmethod
    def _lookup_type(ctx: AstToIrContext, name: Optional[str]):
        """Resolve a type name the way the backends do: exact key, then last segment.

        A `super` is recorded as the name as written (`Base`), while the type is
        registered under its qualified name (`pss_top::Base`), so an exact-key
        lookup alone finds nothing for the common case.
        """
        if not name:
            return None
        if name in ctx.type_map:
            return ctx.type_map[name]
        for key, dt in ctx.type_map.items():
            if key.endswith("::" + name):
                return dt
        return None

    def _expand_generic_refs(self, ctx: AstToIrContext, body: List[ir.Stmt],
                             generics: Dict[str, '_GenericRef'],
                             active: tuple) -> List[ir.Stmt]:
        """Expand generic constraint references in *body*, one statement at a time.

        A statement that is *exactly* a reference is replaced by the instantiated
        body -- a splice, so the body may itself hold a ``foreach`` or a `unique`.
        Any other statement is descended into: its nested statement lists (if/else
        arms, a ``foreach`` body) are expanded the same way, and its expressions are
        handed to :meth:`_expand_generic_expr`, which is the only path that can
        reach a reference used as an operand.

        *active* is the chain of generic constraints currently being expanded; a
        name that reappears in it is a cycle rather than terminating recursion.
        """
        out: List[ir.Stmt] = []
        for stmt in body:
            name = self._generic_ref_name(stmt, generics)
            if name is None:
                out.append(self._expand_generic_in_stmt(
                    ctx, stmt, generics, active))
                continue
            if generics[name].is_value:
                # A value-yielding generic constraint contributes a value to the
                # expression around it (§13.1.2 b); on its own it constrains
                # nothing. Splicing it in as a statement would hand the solver a
                # bare number where it expects a condition, so say so instead.
                ctx.errors.append(
                    f"generic constraint '{name}' yields a value, not a "
                    f"constraint, so it cannot stand alone as a constraint "
                    f"statement; use it in an expression, as in "
                    f"'x == {name}(...)'")
                continue
            expanded = self._instantiate_generic(
                ctx, name, stmt.expr, generics, active)
            if expanded is not None:
                out.extend(expanded)
        return out

    def _expand_generic_in_stmt(self, ctx: AstToIrContext, stmt: ir.Stmt,
                                generics: Dict[str, '_GenericRef'],
                                active: tuple) -> ir.Stmt:
        """Expand references nested inside *stmt*, returning a rewritten copy.

        The walk is over dataclass fields rather than a branch per statement type,
        so a statement kind added later is descended into without another edit
        here. A field holding statements is expanded statement-wise (and so may
        grow, which is why only list-valued statement slots are followed); a field
        holding an expression goes to :meth:`_expand_generic_expr`.
        """
        if not dataclasses.is_dataclass(stmt):
            return stmt
        replacements: Dict[str, Any] = {}
        for field in dataclasses.fields(stmt):
            value = getattr(stmt, field.name)
            if isinstance(value, list) and value and \
                    all(isinstance(v, ir.Stmt) for v in value):
                new_value = self._expand_generic_refs(
                    ctx, value, generics, active)
            elif isinstance(value, ir.Expr):
                new_value = self._expand_generic_expr(
                    ctx, value, generics, active)
            else:
                continue
            if new_value is not value:
                replacements[field.name] = new_value
        if not replacements:
            return stmt
        return dataclasses.replace(stmt, **replacements)

    def _expand_generic_expr(self, ctx: AstToIrContext, expr: ir.Expr,
                             generics: Dict[str, '_GenericRef'],
                             active: tuple) -> ir.Expr:
        """Expand generic constraint references appearing inside *expr*.

        A reference in an operand position has to become a single expression, so
        the instantiated body is folded into a conjunction -- which is what the
        body of a constraint means. This is what lets ``g(x) || y > 5``,
        ``!g(x)`` and the consequent of an implication work; without it the
        reference reached the solver as an `ExprCall` and was rejected there.

        The value-yielding form needs no special case here: its body is a single
        expression, so the same fold returns exactly that expression, and
        ``j == max(k, l)`` falls out of the operand path.
        """
        if isinstance(expr, ir.ExprCall):
            name = self._generic_call_name(expr, generics)
            if name is not None:
                folded = self._instantiate_generic_as_expr(
                    ctx, name, expr, generics, active)
                # On error `folded` is None and the call is left in place; the
                # diagnostic is already recorded, and replacing it with a
                # constant would change what the model means.
                return folded if folded is not None else expr
        if isinstance(expr, list):
            return [self._expand_generic_expr(ctx, e, generics, active)
                    for e in expr]
        if not dataclasses.is_dataclass(expr):
            return expr
        replacements: Dict[str, Any] = {}
        for field in dataclasses.fields(expr):
            value = getattr(expr, field.name)
            if isinstance(value, ir.Expr):
                new_value = self._expand_generic_expr(
                    ctx, value, generics, active)
            elif isinstance(value, list) and value and \
                    all(isinstance(v, ir.Expr) for v in value):
                new_value = [self._expand_generic_expr(ctx, e, generics, active)
                             for e in value]
            else:
                continue
            if new_value is not value:
                replacements[field.name] = new_value
        if not replacements:
            return expr
        return dataclasses.replace(expr, **replacements)

    def _instantiate_generic(self, ctx: AstToIrContext, name: str,
                             call: ir.ExprCall,
                             generics: Dict[str, '_GenericRef'],
                             active: tuple) -> Optional[List[ir.Stmt]]:
        """The body of generic constraint *name*, instantiated for *call*.

        ``None`` means the reference could not be instantiated and a diagnostic
        was recorded. The returned statements are themselves fully expanded, so a
        generic constraint may reference another.
        """
        if name in active:
            self._report_recursion(ctx, name, generics, active)
            return None
        if len(active) >= self._MAX_GENERIC_CONSTRAINT_DEPTH:
            ctx.errors.append(
                f"generic constraint references nested more than "
                f"{self._MAX_GENERIC_CONSTRAINT_DEPTH} deep at '{name}'")
            return None
        decl = generics[name]
        if decl.foreign and self._escaping_field(decl.fn) is not None:
            # A component-scope generic's `self` is the component and a
            # package-scope one is static (§13.1.2), so a field reference in
            # the body does not name a field of the type being constrained.
            # Substituting the body here would silently rebind it to a
            # same-named field of the referencing type, or to nothing --
            # either way a constraint that reads as correct and is not.
            ctx.errors.append(
                f"generic constraint '{name}' declared in "
                f"'{decl.scope}' references '{self._escaping_field(decl.fn)}' "
                f"from its declaring scope, which is not in scope where it "
                f"is referenced; pass it as a parameter instead")
            return None
        target = decl.fn
        params = [a.arg for a in (target.args.args if target.args else [])]
        # An argument is an expression at the *reference* site, so a reference
        # inside it is expanded in the caller's context -- with *active* as it
        # stands here, not extended by this name. Expanding arguments after
        # substitution instead would make `plus1(plus1(k))` look like `plus1`
        # reaching itself, which is a sibling reference, not recursion.
        args = [self._expand_generic_expr(ctx, a, generics, active)
                for a in call.args]
        if len(args) != len(params):
            # The linker checks arity, so reaching here means the two
            # disagree about the signature -- emit nothing rather than a
            # body with unbound parameters in it.
            ctx.errors.append(
                f"generic constraint '{name}' takes {len(params)} "
                f"argument(s), but {len(args)} were given")
            return None
        for pname, arg in zip(params, args):
            if pname in (target.metadata.get('_generic_const_params') or ()) \
                    and self._reads_a_field(arg):
                # A `const` parameter promises its actual is a constant, which is
                # what lets one size or index an array. A random actual makes that
                # promise false at the one moment it matters -- after solving --
                # and nothing downstream would notice.
                ctx.errors.append(
                    f"generic constraint '{name}' declares parameter "
                    f"'{pname}' const, so its argument must be a constant, but "
                    f"a random field was passed")
                return None
        bindings = dict(zip(params, args))
        expanded = [self._subst_locals(s, bindings) for s in target.body]
        prov = self._generic_provenance(ctx, name, decl, active)
        if decl.is_value:
            # The value form's body is one expression, and a reference *inside*
            # it is in expression position too -- `constraint int m3(...) max(a,
            # max(b,c));`. Expanding it as a statement list would route that
            # inner reference through the bare-statement path, which rejects a
            # value form. So expand the expression as an expression.
            stmt = expanded[0] if expanded else None
            if not isinstance(stmt, ir.StmtExpr):
                return []
            # The provenance goes on the *expression*, not the wrapping
            # statement: the statement is a carrier that
            # `_instantiate_generic_as_expr` unwraps, so anything recorded on it
            # is dropped before the substitution reaches the use site.
            return [ir.StmtExpr(expr=self._stamp_provenance(
                self._expand_generic_expr(
                    ctx, stmt.expr, generics, active + (name,)), prov))]
        return [self._stamp_provenance(s, prov) for s in
                self._expand_generic_refs(
                    ctx, expanded, generics, active + (name,))]

    def _instantiate_generic_as_expr(
        self,
        ctx: AstToIrContext,
        name: str,
        call: ir.ExprCall,
        generics: Dict[str, '_GenericRef'],
        active: tuple,
    ) -> Optional[ir.Expr]:
        """*name*'s body as one boolean expression, for an operand position."""
        stmts = self._instantiate_generic(ctx, name, call, generics, active)
        if stmts is None:
            return None
        exprs: List[ir.Expr] = []
        for stmt in stmts:
            if not isinstance(stmt, ir.StmtExpr):
                # `foreach`, `unique` and if/else have no value, so a body
                # containing one cannot be folded into an operand. Reject it
                # rather than dropping the part that does not fit.
                ctx.errors.append(
                    f"generic constraint '{name}' is referenced inside an "
                    f"expression, but its body contains a "
                    f"{type(stmt).__name__.replace('Stmt', '').lower()} "
                    f"constraint, which has no value; reference it as a "
                    f"statement of its own instead")
                return None
            exprs.append(stmt.expr)
        if not exprs:
            # An empty body constrains nothing, and `true` is the identity of
            # the conjunction this fold builds.
            return ir.ExprConstant(value=True)
        folded = exprs[0]
        for operand in exprs[1:]:
            folded = ir.ExprBin(lhs=folded, op=ir.BinOp.And, rhs=operand)
        # The fold is a new node, so it carries no provenance of its own, while
        # the statements it was built from do. Move it up: the conjunction *is*
        # the instantiation as far as the surrounding expression is concerned,
        # and provenance that only exists on discarded wrappers is provenance
        # the model does not have.
        prov = next((s.provenance for s in stmts
                     if s.provenance is not None), None)
        return self._stamp_provenance(folded, prov) if prov is not None \
            else folded

    #: The name recorded in ``Provenance.pass_name`` for a statement or
    #: expression that a generic constraint reference put there.
    GENERIC_CONSTRAINT_PASS = 'generic_constraints'

    def _generic_provenance(self, ctx: AstToIrContext, name: str,
                            decl: '_GenericRef',
                            active: tuple) -> 'ir.Provenance':
        """Provenance for one instantiation of generic constraint *name*.

        ``source_names`` is the reference chain, outermost first, ending in the
        declaration actually being instantiated. The chain matters because a
        generic constraint may be reachable only through another one, and "this
        came from `lt`" is a much weaker answer than "this came from `lt`,
        reached from `in_range`".

        Names rather than ``source_nodes``, deliberately. Pointing at the
        declaration node would be the more precise link, but it also turns every
        instantiated statement into a back-edge into the declaration's subtree,
        which any dataclass-generic walk then follows -- and a recursive generic
        constraint would make that a cycle. Two of this suite's own IR walkers
        found the declaration's unexpanded body through such a link and reported
        it as a leftover reference. Provenance that changes what walking the IR
        finds is not worth the precision; the declaring scope goes in
        ``description`` instead, which is what disambiguates a repeated name.

        ``site`` is what keeps two references to one declaration apart. Their
        bodies are spliced into the same constraint and may be textually
        identical after substitution, so without a per-reference number the
        lowering cannot answer "which of the two references produced this?".
        """
        ctx.generic_ref_sites += 1
        via = " reached from " + " -> ".join(
            f"'{n}'" for n in reversed(active)) if active else ""
        return ir.Provenance(
            pass_name=self.GENERIC_CONSTRAINT_PASS,
            source_names=list(active) + [name],
            description=(f"generic constraint '{name}' declared in "
                         f"'{decl.scope}'{via}"),
            site=ctx.generic_ref_sites)

    @staticmethod
    def _stamp_provenance(node: Any, prov: 'ir.Provenance') -> Any:
        """*node* with *prov* recorded on it, unless it already has provenance.

        First writer wins, and the first writer is always the innermost
        instantiation -- a nested reference is expanded before the reference
        that reached it returns. That is the right precedence: the innermost
        chain is strictly the more specific answer, and it already names the
        outer references in ``source_nodes``.

        A copy rather than an in-place assignment, because an unsubstituted part
        of a declaration's body is shared with the declaration itself and with
        every other reference to it. Writing through would give two references
        one provenance and label the declaration as its own instantiation.
        """
        if getattr(node, 'provenance', None) is not None:
            return node
        if not dataclasses.is_dataclass(node):
            return node
        return dataclasses.replace(node, provenance=prov)

    @classmethod
    def _generic_ref_name(cls, stmt: ir.Stmt,
                          generics: Dict[str, '_GenericRef']) -> Optional[str]:
        """The generic constraint *stmt* is a bare reference to, or ``None``."""
        if not isinstance(stmt, ir.StmtExpr):
            return None
        if not isinstance(stmt.expr, ir.ExprCall):
            return None
        return cls._generic_call_name(stmt.expr, generics)

    @staticmethod
    def _generic_call_name(call: ir.ExprCall,
                           generics: Dict[str, '_GenericRef']) -> Optional[str]:
        """The generic constraint *call* references, or ``None``.

        Two spellings reach here. An unqualified name becomes a call on ``self``,
        because that is how every unqualified name in a constraint is translated.
        A package-qualified one (`p::lt(x, 20)`) has no ``self`` to hang off and
        becomes a call on an unresolved reference carrying the qualified name --
        see :meth:`_translate_expr_ref_static_rooted`.
        """
        func = call.func
        if isinstance(func, ir.ExprAttribute) and \
                isinstance(func.value, ir.TypeExprRefSelf):
            return func.attr if func.attr in generics else None
        if isinstance(func, ir.ExprRefUnresolved):
            return func.name if func.name in generics else None
        return None

    @classmethod
    def _escaping_field(cls, fn: ir.Function) -> Optional[str]:
        """The first field this generic constraint's body reads off ``self``.

        ``None`` when the body only uses its parameters, which is the only shape
        that may be expanded into a scope other than the declaring one.
        """
        found: List[str] = []

        def walk(node, depth=0):
            if found or depth > 32 or node is None:
                return
            if isinstance(node, ir.ExprAttribute) and \
                    isinstance(node.value, ir.TypeExprRefSelf):
                found.append(node.attr)
                return
            if isinstance(node, (list, tuple)):
                for elem in node:
                    walk(elem, depth + 1)
                return
            if not dataclasses.is_dataclass(node):
                return
            for field in dataclasses.fields(node):
                walk(getattr(node, field.name), depth + 1)

        walk(fn.body)
        return found[0] if found else None

    @classmethod
    def _reads_a_field(cls, node) -> bool:
        """Does *node* read a field off ``self``?

        Used as the test for "involves randomization" -- in a constraint, a field
        of the enclosing type is what the solver assigns, while a parameter, a
        literal and a folded constant are all fixed before solving. It is
        conservative in one direction: a *non-rand* attribute reads as random
        here. That costs a false diagnostic on a shape no test exercises yet, and
        the alternative -- treating an unknown reference as constant -- would let
        the cases this predicate exists to catch through silently.
        """
        if isinstance(node, ir.ExprAttribute) and \
                isinstance(node.value, ir.TypeExprRefSelf):
            return True
        if isinstance(node, (list, tuple)):
            return any(cls._reads_a_field(n) for n in node)
        if not dataclasses.is_dataclass(node):
            return False
        return any(cls._reads_a_field(getattr(node, f.name))
                   for f in dataclasses.fields(node))

    def _report_recursion(self, ctx: AstToIrContext, name: str,
                          generics: Dict[str, '_GenericRef'],
                          active: tuple) -> None:
        """Diagnose a generic constraint reached from itself, per §13.1.2 d.

        Recursion is *legal* when "gated by an expression that does not involve
        randomization", so three different things bring us here and they want
        three different messages. Reporting all of them as "refers to itself" is
        wrong in the way that matters most for the legal case: it tells the author
        of correct code that their code is circular.

        The gate is the condition of the if/implication the recursive reference
        sits under, found by walking the declaration's own body.
        """
        chain = ' -> '.join(active + (name,))
        gate = self._recursion_gate(generics[name].fn, set(active) | {name})
        if gate is None:
            ctx.errors.append(
                f"generic constraint '{name}' refers to itself with no gate "
                f"(via {chain}); recursion must be gated by an expression that "
                f"does not involve randomization (PSS 3.1 §13.1.2 d)")
        elif self._reads_a_field(gate):
            ctx.errors.append(
                f"generic constraint '{name}' recurses (via {chain}) under a "
                f"gate that involves randomization; the gate must be resolvable "
                f"before solving (PSS 3.1 §13.1.2 d)")
        else:
            # Legal per §13.1.2 d, and not yet supported: unrolling it means
            # evaluating the gate during elaboration. Said plainly, because this
            # is the one bucket where the input is correct.
            ctx.errors.append(
                f"generic constraint '{name}' recurses (via {chain}) under a "
                f"non-random gate, which PSS 3.1 §13.1.2 d permits but this "
                f"compiler does not yet unroll")

    @classmethod
    def _recursion_gate(cls, fn: ir.Function,
                        cycle: Set[str]) -> Optional[ir.Expr]:
        """The condition guarding a reference in *fn*'s body to a name in *cycle*.

        ``None`` means the reference is reached unconditionally -- so there is no
        gate at all, rather than a gate that happens to be constant.
        """
        found: List[ir.Expr] = []

        def walk(node, gate: Optional[ir.Expr], depth: int = 0) -> None:
            if found or depth > 32 or node is None:
                return
            if isinstance(node, ir.ExprCall):
                called = node.func
                called_name = getattr(called, 'attr', None) or \
                    getattr(called, 'name', None)
                if called_name == 'implies' and len(node.args) == 2:
                    # An implication lowers to a call, so its consequent is
                    # gated even though no `StmtIf` is involved.
                    walk(node.args[1], node.args[0], depth + 1)
                    return
                if called_name in cycle:
                    found.append(gate)
                    return
            if isinstance(node, ir.StmtIf):
                walk(node.body, node.test, depth + 1)
                walk(node.orelse, node.test, depth + 1)
                return
            if isinstance(node, (list, tuple)):
                for elem in node:
                    walk(elem, gate, depth + 1)
                return
            if not dataclasses.is_dataclass(node):
                return
            for field in dataclasses.fields(node):
                walk(getattr(node, field.name), gate, depth + 1)

        walk(fn.body, None)
        return found[0] if found else None

    @classmethod
    def _subst_locals(cls, node, bindings: Dict[str, ir.Expr]):
        """Copy *node*, replacing each ``ExprRefLocal`` named in *bindings*.

        A generic constraint's parameters are translated as locals (see
        :meth:`_translate_constraint_block`), so instantiating the body is a
        substitution of locals by the argument expressions. The copy is deep: two
        references to one constraint must not share expression nodes, or a later
        rewrite of one would silently change the other.
        """
        if isinstance(node, ir.ExprRefLocal) and node.name in bindings:
            return copy.deepcopy(bindings[node.name])
        if isinstance(node, list):
            return [cls._subst_locals(n, bindings) for n in node]
        if not dataclasses.is_dataclass(node):
            return node
        replacements = {}
        for field in dataclasses.fields(node):
            value = getattr(node, field.name)
            new_value = cls._subst_locals(value, bindings)
            if new_value is not value:
                replacements[field.name] = new_value
        if not replacements:
            return copy.deepcopy(node)
        return dataclasses.replace(copy.deepcopy(node), **replacements)

    def _hier_id_to_expr(self, hier_id) -> 'ir.Expr':
        """Convert an ExprHierarchicalId to a chain of ExprAttribute nodes.

        Used for ``bind lhs rhs;`` statement LHS/RHS which are
        ``ExprMemberPathElem`` chains (e.g. ``p.out_data`` -> ``self.p.out_data``).
        """
        result: ir.Expr = ir.TypeExprRefSelf()
        if hier_id is None:
            return result
        for i in range(hier_id.numElems()):
            elem = hier_id.getElem(i)
            id_obj = elem.getId() if hasattr(elem, 'getId') else None
            if id_obj is None:
                continue
            name = id_obj.getId() if hasattr(id_obj, 'getId') else str(id_obj)
            result = ir.ExprAttribute(value=result, attr=name)
        return result

    def _translate_join_spec(self, join_spec) -> Optional['ir.JoinSpec']:
        """Convert a PSS ActivityJoinSpec to IR JoinSpec."""
        if join_spec is None:
            return None
        if isinstance(join_spec, pss_ast.ActivityJoinSpecBranch):
            return ir.JoinSpec(kind='branch')
        if isinstance(join_spec, pss_ast.ActivityJoinSpecFirst):
            count = self._translate_expression(None, join_spec.getCount()) if hasattr(join_spec, 'getCount') else None
            return ir.JoinSpec(kind='first', count=count)
        if isinstance(join_spec, pss_ast.ActivityJoinSpecNone):
            return ir.JoinSpec(kind='none')
        if isinstance(join_spec, pss_ast.ActivityJoinSpecSelect):
            count = self._translate_expression(None, join_spec.getCount()) if hasattr(join_spec, 'getCount') else None
            return ir.JoinSpec(kind='select', count=count)
        return ir.JoinSpec(kind='all')

    def _translate_field_ref(self, ctx: AstToIrContext, field_ref: pss_ast.FieldRef) -> Optional[ir.Field]:
        """Translate a PSS FieldRef (input/output flow-object reference) to IR Field."""
        name_node = field_ref.getName()
        field_name = name_node.getId() if hasattr(name_node, 'getId') else str(name_node)
        is_input = field_ref.getIs_input()
        flow_type = self._translate_data_type(ctx, field_ref.getType())
        kind = ir.FieldKind.Input if is_input else ir.FieldKind.Output
        return ir.Field(name=field_name, datatype=flow_type, kind=kind)

    def _translate_field_claim(self, ctx: 'AstToIrContext', field_claim: 'pss_ast.FieldClaim') -> Optional['ir.Field']:
        """Translate a PSS FieldClaim (lock/share resource reference) to an IR Field.

        ``lock T name;`` -> Field(kind=FieldKind.Lock)
        ``share T name;`` -> Field(kind=FieldKind.Share)
        """
        name_node = field_claim.getName()
        field_name = name_node.getId() if hasattr(name_node, 'getId') else str(name_node)
        resource_type = self._translate_data_type(ctx, field_claim.getType())
        kind = ir.FieldKind.Lock if field_claim.getIs_lock() else ir.FieldKind.Share
        return ir.Field(name=field_name, datatype=resource_type, kind=kind)

    def _translate_struct(self, ctx: AstToIrContext, struct: pss_ast.Struct, namespace_prefix: str = "") -> ir.DataTypeStruct:
        """Translate a PSS struct to IR DataTypeStruct

        Args:
            ctx: Translation context
            struct: PSS struct AST node
            namespace_prefix: Namespace prefix for package-scoped types

        Returns:
            IR DataTypeStruct
        """
        self._record_template_params(ctx, struct)

        # Extract struct name
        name_node = struct.getName()
        if isinstance(name_node, pss_ast.ExprId):
            struct_name = name_node.getId()
        else:
            struct_name = str(name_node)

        qualified_name = f"{namespace_prefix}{struct_name}"

        if self.debug:
            self.logger.debug(f"Translating struct: {qualified_name}")

        # Create IR struct
        struct_ir = ir.DataTypeStruct(name=qualified_name, super=None)
        struct_ir.doc = ast_doc(struct)

        # Set flow_kind from StructKind (buffer/stream/state/resource)
        if hasattr(struct, 'getKind'):
            kind = struct.getKind()
            _flow_kind_map = {
                pss_ast.StructKind.Buffer:   "buffer",
                pss_ast.StructKind.Stream:   "stream",
                pss_ast.StructKind.State:    "state",
                pss_ast.StructKind.Resource: "resource",
            }
            struct_ir.flow_kind = _flow_kind_map.get(kind)

        # Register in type map
        ctx.add_type(qualified_name, struct_ir)
        if namespace_prefix:
            ctx.add_type(struct_name, struct_ir)

        # Push scope and type-chain name for annotation matching
        ctx.push_scope(struct_ir)
        self._type_chain_stack.append(struct_name)

        # Handle inheritance
        super_t = struct.getSuper_t()
        if super_t is not None:
            super_name = self._type_identifier_name(super_t)
            if super_name:
                struct_ir.super = ir.DataTypeRef(ref_name=super_name)

        # Translate children (fields, exec blocks, and constraints)
        for child in struct.children():
            if child is None:
                continue

            if isinstance(child, pss_ast.Field):
                field = self._translate_field(ctx, child)
                if field:
                    struct_ir.fields.append(field)
            elif isinstance(child, pss_ast.ExecBlock):
                kind = child.getKind()
                if kind == pss_ast.ExecKind.ExecKind_PreSolve:
                    stmts = self._translate_exec_scope(ctx, child)
                    func = ir.Function(name='pre_solve', is_async=False, body=stmts)
                    struct_ir.functions.append(func)
                elif kind == pss_ast.ExecKind.ExecKind_PostSolve:
                    stmts = self._translate_exec_scope(ctx, child)
                    func = ir.Function(name='post_solve', is_async=False, body=stmts)
                    struct_ir.functions.append(func)
            elif isinstance(child, pss_ast.ConstraintBlock):
                constraint_func = self._translate_constraint_block(ctx, child, struct_ir)
                if constraint_func:
                    struct_ir.functions.append(constraint_func)
            elif isinstance(child, pss_ast.GenericConstraintDeclValue):
                value_func = self._translate_generic_value_constraint(ctx, child)
                if value_func:
                    struct_ir.functions.append(value_func)
            elif isinstance(child, pss_ast.Covergroup):
                cg = self._translate_covergroup(ctx, child)
                if cg is not None:
                    struct_ir.covergroups.append(cg)

        # Flush any `rand int in [range]` domain constraints onto this struct.
        self._flush_range_constraints(struct_ir)

        # Pop type-chain name for struct
        self._type_chain_stack.pop()

        # Tag state structs that use the `initial` built-in in a constraint implication.
        # pssparser injects the `bool initial;` built-in field natively so the
        # linker accepts `initial`; here we record whether any constraint body
        # references it.
        if struct_ir.flow_kind == "state":
            struct_ir.has_initial_constraint = self._struct_references_initial(struct_ir)
            # Ensure `initial` field defaults to True (it is set False at runtime for
            # non-initial states; True is the correct starting value per PSS LRM).
            for f in struct_ir.fields:
                if f.name == "initial":
                    f.initial_value = ir.ExprConstant(value=1)
                    break

        # Pop scope
        ctx.pop_scope()

        return struct_ir

    def _struct_references_initial(self, struct_ir) -> bool:
        """Return True if any constraint in this state struct references the `initial` field.

        pssparser injects the `bool initial;` built-in field into state struct
        bodies natively, so the constraint `constraint initial -> val == X;` is
        translatable. We check whether any constraint function body uses
        `initial` as a field ref.
        """
        from zuspec.ir.core.expr import ExprAttribute, TypeExprRefSelf, ExprRefUnresolved
        for fn in struct_ir.functions:
            if not fn.metadata.get('_is_constraint'):
                continue
            for stmt in fn.body:
                if self._expr_has_name(stmt, 'initial'):
                    return True
        return False

    def _expr_has_name(self, node, name: str) -> bool:
        """Recursively check whether any ExprAttribute/ExprRefUnresolved references `name`."""
        from zuspec.ir.core import expr as ir_expr
        if node is None:
            return False
        if isinstance(node, ir_expr.ExprAttribute) and node.attr == name:
            return True
        if isinstance(node, ir_expr.ExprRefUnresolved) and node.name == name:
            return True
        # Recurse into scalar child attributes
        for attr in ('expr', 'lhs', 'rhs', 'cond', 'value', 'func',
                     'test', 'body', 'orelse', 'operand', 'val'):
            child = getattr(node, attr, None)
            if child is not None and not isinstance(child, (str, int, float, bool)):
                if self._expr_has_name(child, name):
                    return True
        # Recurse into list child attributes
        for attr in ('stmts', 'body', 'args', 'elts', 'ranges', 'body_exprs'):
            lst = getattr(node, attr, None)
            if isinstance(lst, list):
                for item in lst:
                    if self._expr_has_name(item, name):
                        return True
        return False

    def _forall_collection_from_type(self, type_id) -> Optional[ir.Expr]:
        """Build a self-relative collection expression from a forall's type node.

        For the `forall (it : coll)` form (no `in`), the type position actually
        names the collection field, e.g. `coll` -> self.coll, `a.b` -> self.a.b.
        """
        if type_id is None:
            return None
        ti = type_id.getType_id() if hasattr(type_id, 'getType_id') else None
        if ti is None:
            return None
        names: List[str] = []
        for k in range(ti.numElems()):
            elem = ti.getElem(k)
            if elem is None:
                continue
            id_obj = elem.getId()
            if isinstance(id_obj, pss_ast.ExprId):
                names.append(id_obj.getId())
            elif id_obj is not None:
                names.append(str(id_obj))
        if not names:
            return None
        coll: ir.Expr = ir.TypeExprRefSelf()
        for name in names:
            coll = ir.ExprAttribute(value=coll, attr=name)
        return coll

    def _collect_constraint_stmt(
        self,
        ctx: AstToIrContext,
        stmt,
        body: List[ir.Stmt],
    ) -> None:
        """Translate a single PSS constraint statement and append IR stmts to body."""
        if isinstance(stmt, pss_ast.ConstraintStmtExpr):
            expr_node = stmt.getExpr()
            if expr_node is None:
                return
            ir_expr = self._translate_expression(ctx, expr_node)
            if ir_expr is not None:
                body.append(ir.StmtExpr(expr=ir_expr))

        elif isinstance(stmt, pss_ast.ConstraintStmtImplication):
            cond_node = stmt.getCond()
            if cond_node is None:
                return
            cond_expr = self._translate_expression(ctx, cond_node)
            if cond_expr is None:
                return
            for j in range(stmt.numConstraints()):
                sub = stmt.getConstraint(j)
                if sub and isinstance(sub, pss_ast.ConstraintStmtExpr):
                    sub_expr_node = sub.getExpr()
                    if sub_expr_node is not None:
                        sub_ir = self._translate_expression(ctx, sub_expr_node)
                        if sub_ir is not None:
                            body.append(ir.StmtExpr(expr=ir.ExprCall(
                                func=ir.ExprRefUnresolved(name='implies'),
                                args=[cond_expr, sub_ir],
                            )))

        elif isinstance(stmt, pss_ast.ConstraintStmtIf):
            cond_node = stmt.getCond()
            if cond_node is None:
                return
            cond_expr = self._translate_expression(ctx, cond_node)
            if cond_expr is None:
                return
            true_stmts: List[ir.Stmt] = []
            true_c = stmt.getTrue_c()
            if true_c is not None:
                for j in range(true_c.numConstraints()):
                    sub = true_c.getConstraint(j)
                    if sub and isinstance(sub, pss_ast.ConstraintStmtExpr):
                        sub_expr_node = sub.getExpr()
                        if sub_expr_node is not None:
                            sub_ir = self._translate_expression(ctx, sub_expr_node)
                            if sub_ir is not None:
                                true_stmts.append(ir.StmtExpr(expr=sub_ir))
            false_stmts: List[ir.Stmt] = []
            false_c = stmt.getFalse_c()
            if false_c is not None:
                for j in range(false_c.numConstraints()):
                    sub = false_c.getConstraint(j)
                    if sub and isinstance(sub, pss_ast.ConstraintStmtExpr):
                        sub_expr_node = sub.getExpr()
                        if sub_expr_node is not None:
                            sub_ir = self._translate_expression(ctx, sub_expr_node)
                            if sub_ir is not None:
                                false_stmts.append(ir.StmtExpr(expr=sub_ir))
            if true_stmts:
                body.append(ir.StmtIf(
                    test=cond_expr,
                    body=true_stmts,
                    orelse=false_stmts,
                ))

        # ConstraintStmtForeach is a subclass of ConstraintScope, so it MUST be
        # checked before the generic ConstraintScope branch.
        elif isinstance(stmt, pss_ast.ConstraintStmtForeach):
            it_node = stmt.getIt()   # element-style: foreach (e : data)
            idx_node = stmt.getIdx() # index-style:   foreach (data[i])
            if it_node is not None:
                var_name_obj = it_node.getName()
            elif idx_node is not None:
                var_name_obj = idx_node.getName()
            else:
                return
            if var_name_obj is None:
                return
            iter_var_name = (var_name_obj.getId()
                             if hasattr(var_name_obj, 'getId') else str(var_name_obj))

            collection_expr_node = stmt.getExpr()
            if collection_expr_node is None:
                return
            collection_ir = self._translate_expression(ctx, collection_expr_node)
            if collection_ir is None:
                return

            ctx.local_vars.add(iter_var_name)
            foreach_body: List[ir.Stmt] = []
            for j in range(stmt.numConstraints()):
                sub = stmt.getConstraint(j)
                if sub is not None:
                    self._collect_constraint_stmt(ctx, sub, foreach_body)
            ctx.local_vars.discard(iter_var_name)

            if foreach_body:
                body.append(ir.StmtForeach(
                    target=ir.ExprRefLocal(name=iter_var_name),
                    iter=collection_ir,
                    body=foreach_body,
                ))

        # ConstraintStmtForall is a subclass of ConstraintScope, so it MUST be
        # checked before the generic ConstraintScope branch. `forall` iterates the
        # elements of a collection, so it lowers to the same IR StmtForeach as an
        # element-style foreach. Two forms are accepted:
        #   forall (it : T in coll)  -> collection is `coll` (ref_path)
        #   forall (it : coll)       -> collection is `coll` itself (the field)
        elif isinstance(stmt, pss_ast.ConstraintStmtForall):
            iter_id_obj = stmt.getIterator_id()
            if iter_id_obj is None:
                return
            iter_var_name = (iter_id_obj.getId()
                             if hasattr(iter_id_obj, 'getId') else str(iter_id_obj))

            ref_path_node = stmt.getRef_path()
            if ref_path_node is not None:
                collection_ir = self._translate_expression(ctx, ref_path_node)
            else:
                # No `in <collection>`: the type position names the collection
                # itself (a self-relative field path).
                collection_ir = self._forall_collection_from_type(stmt.getType_id())
            if collection_ir is None:
                return

            ctx.local_vars.add(iter_var_name)
            forall_body: List[ir.Stmt] = []
            for j in range(stmt.numConstraints()):
                sub = stmt.getConstraint(j)
                # Skip the synthetic iterator field the parser places at index 0.
                if sub is not None and not isinstance(sub, pss_ast.ConstraintStmtField):
                    self._collect_constraint_stmt(ctx, sub, forall_body)
            ctx.local_vars.discard(iter_var_name)

            if forall_body:
                body.append(ir.StmtForeach(
                    target=ir.ExprRefLocal(name=iter_var_name),
                    iter=collection_ir,
                    body=forall_body,
                ))

        elif isinstance(stmt, pss_ast.ConstraintScope):
            for j in range(stmt.numConstraints()):
                sub = stmt.getConstraint(j)
                if sub is not None:
                    self._collect_constraint_stmt(ctx, sub, body)

        elif isinstance(stmt, pss_ast.ConstraintStmtUnique):
            var_names = []
            for j in range(stmt.numList()):
                hid = stmt.getList(j)
                if hid is not None and hid.numElems() > 0:
                    # Take the last element as the field name
                    last = hid.getElem(hid.numElems() - 1)
                    if hasattr(last, 'getId'):
                        id_obj = last.getId()
                        name = id_obj.getId() if isinstance(id_obj, pss_ast.ExprId) else str(id_obj)
                    else:
                        name = str(last)
                    var_names.append(name)
            if len(var_names) >= 2:
                body.append(ir.StmtUnique(vars=var_names))

        elif isinstance(stmt, (pss_ast.ConstraintStmtDefault,
                               pss_ast.ConstraintStmtDefaultDisable)):
            # §13.3 rule g: neither form may appear under a generic constraint.
            # Elsewhere `default` is simply not implemented yet and falls through
            # to the skip below -- but *here* skipping is not a missing feature,
            # it is a silently weaker model: a generic constraint whose body is
            # `default x == 3; x < 100;` would drop the default and compile, with
            # `x < 100` the only thing left.
            if ctx.generic_constraint_name is not None:
                kind = "default disable" if isinstance(
                    stmt, pss_ast.ConstraintStmtDefaultDisable) else "default"
                ctx.errors.append(
                    f"'{kind}' may not be used inside generic constraint "
                    f"'{ctx.generic_constraint_name}' (PSS 3.1 §13.3 g)")
            elif self.debug:
                self.logger.debug("`default` constraints are not implemented")

        else:
            if self.debug:
                self.logger.debug(
                    f"Unsupported constraint stmt type: {type(stmt).__name__}; skipping"
                )

    def _translate_constraint_block(
        self,
        ctx: AstToIrContext,
        constraint_block: pss_ast.ConstraintBlock,
        owner: ir.DataTypeStruct,
    ) -> Optional[ir.Function]:
        """Translate a PSS ConstraintBlock to an IR Function marked as a constraint.

        Creates an `ir.Function` with `metadata={'_is_constraint': True}` whose body
        contains `StmtExpr` nodes for each translatable constraint statement.

        Args:
            ctx: Translation context
            constraint_block: PSS ConstraintBlock AST node
            owner: The enclosing struct/action IR type (used for auto-naming)

        Returns:
            IR Function if any constraint statements were translated, else None
        """
        # Determine the constraint function name
        raw_name = constraint_block.getName() if hasattr(constraint_block, 'getName') else None
        if raw_name:
            func_name = raw_name
        else:
            # Auto-generate a unique name based on position in owner's function list
            idx = sum(1 for f in owner.functions if f.metadata.get('_is_constraint'))
            func_name = f'_c_{idx}'

        # A generic constraint is a template, not a constraint in force (§13.1.2).
        # Its parameters are in scope over its body, so register them as locals for
        # the duration -- otherwise `lim` in `constraint c(int lim) { x < lim; }`
        # translates to `self.lim`, a field the type does not have.
        is_generic = self._is_generic_constraint(constraint_block)
        params = self._translate_generic_constraint_params(ctx, constraint_block)
        shadowed = {p.arg for p in params} - ctx.local_vars
        ctx.local_vars |= shadowed
        outer_generic = ctx.generic_constraint_name
        if is_generic:
            ctx.generic_constraint_name = func_name

        body: List[ir.Stmt] = []

        for i in range(constraint_block.numConstraints()):
            stmt = constraint_block.getConstraint(i)
            if stmt is None:
                continue

            if isinstance(stmt, pss_ast.ConstraintStmtForeach):
                # foreach has special context management (iterator variable).
                # Element-style: foreach (e : data)  → getIt() returns the var
                # Index-style:   foreach (data[i])   → getIdx() returns the var
                it_node = stmt.getIt()
                idx_node = stmt.getIdx()
                if it_node is not None:
                    var_name_obj = it_node.getName()
                elif idx_node is not None:
                    var_name_obj = idx_node.getName()
                else:
                    continue
                if var_name_obj is None:
                    continue
                iter_var_name = var_name_obj.getId() if hasattr(var_name_obj, 'getId') else str(var_name_obj)

                collection_expr_node = stmt.getExpr()
                if collection_expr_node is None:
                    continue
                collection_ir = self._translate_expression(ctx, collection_expr_node)
                if collection_ir is None:
                    continue

                ctx.local_vars.add(iter_var_name)
                foreach_body: List[ir.Stmt] = []
                for j in range(stmt.numConstraints()):
                    sub = stmt.getConstraint(j)
                    if sub is not None:
                        self._collect_constraint_stmt(ctx, sub, foreach_body)
                ctx.local_vars.discard(iter_var_name)

                if foreach_body:
                    body.append(ir.StmtForeach(
                        target=ir.ExprRefLocal(name=iter_var_name),
                        iter=collection_ir,
                        body=foreach_body,
                    ))
            else:
                self._collect_constraint_stmt(ctx, stmt, body)

        ctx.local_vars -= shadowed
        ctx.generic_constraint_name = outer_generic

        if not body and not is_generic:
            return None

        if is_generic:
            # Deliberately NOT `_is_constraint`: a generic constraint is inert
            # until referenced, so nothing may collect it into a solve problem.
            # The body and parameters are kept so a reference can instantiate
            # them once pssparser resolves references (see
            # docs/design/generic-constraints-system-tests.md, phase 1a).
            #
            # Registered even with an *empty* body, unlike a fixed constraint.
            # An unregistered declaration is not merely absent: the reference to
            # it then goes unrecognized and reaches the backend as an
            # unexpanded call, so a body made only of items this translator does
            # not handle got blamed on the solver. Empty is also legal on its own
            # -- `constraint g() {}` constrains nothing.
            return ir.Function(
                name=func_name,
                is_async=False,
                args=ir.Arguments(args=params),
                body=body,
                metadata={
                    '_is_generic_constraint': True,
                    '_generic_const_params': self._const_param_names(
                        constraint_block),
                    '_generic_signature': self._generic_signature(
                        ctx, constraint_block, params, is_value=False),
                },
            )

        return ir.Function(
            name=func_name,
            is_async=False,
            body=body,
            metadata={'_is_constraint': True},
        )

    @staticmethod
    def _is_generic_constraint(constraint_block) -> bool:
        """Is this block a generic constraint (§13.1.2) rather than one in force?

        Two spellings reach here. ``constraint c(int lim) {...}`` parses to a
        ``GenericConstraintDeclBool``, which *subclasses* ``ConstraintBlock`` --
        which is exactly why it used to be swallowed by the ordinary constraint
        path. ``dynamic constraint c {...}`` is the deprecated spelling of the
        zero-parameter form and arrives as a plain block with ``is_dynamic`` set.
        """
        if isinstance(constraint_block, pss_ast.GenericConstraintDeclBool):
            return True
        return bool(getattr(constraint_block, 'getIs_dynamic', lambda: False)())

    def _translate_generic_constraint_params(
        self,
        ctx: AstToIrContext,
        constraint_block,
    ) -> List[ir.Arg]:
        """Translate a generic constraint's parameter list; ``[]`` if it has none.

        A ``numeric`` parameter carries no declared type -- the type is reified
        from the arguments at each reference site -- so it is annotated ``int``
        here as a placeholder until reference resolution lands (phase 1a).
        """
        if not hasattr(constraint_block, 'numParameters'):
            return []
        params: List[ir.Arg] = []
        for i in range(constraint_block.numParameters()):
            param = constraint_block.getParameter(i)
            if param is None:
                continue
            name_node = param.getName()
            name = name_node.getId() if isinstance(name_node, pss_ast.ExprId) else str(name_node)
            if param.getIs_numeric():
                ptype = ctx.type_map.get('int')
            else:
                ptype = self._translate_data_type(ctx, param.getType())
            params.append(ir.Arg(arg=name, annotation=ptype))
        return params

    @staticmethod
    def _const_param_names(decl) -> tuple:
        """The names of *decl*'s ``const`` parameters.

        ``const`` on a generic constraint parameter is in the grammar (Syntax58)
        but §13.1.2 says nothing about what it means. Taken here as the reading
        that makes it useful and that GC-2.5 assumes: the actual must be a
        constant, which is what lets such a parameter size an array or index one.
        Checked at the reference, in :meth:`_instantiate_generic`.
        """
        if not hasattr(decl, 'numParameters'):
            return ()
        out = []
        for i in range(decl.numParameters()):
            param = decl.getParameter(i)
            if param is None or not param.getIs_const():
                continue
            name_node = param.getName()
            out.append(name_node.getId() if isinstance(name_node, pss_ast.ExprId)
                       else str(name_node))
        return tuple(out)

    def _generic_signature(self, ctx: AstToIrContext, decl,
                           params: List[ir.Arg], *, is_value: bool) -> tuple:
        """*decl*'s signature, for the shadowing check in §13.1.2 c.

        Recorded as plain comparable text rather than as types, because the only
        consumer asks "do these two match?" and needs to *print* both when they
        do not.

        The return type appears here and nowhere else. W2 deliberately does not
        record it for substitution -- each reference is typed by its own
        arguments, which is what gives per-signature specialization for free --
        but §13.1.2 c requires the return types of a shadowing pair to match, and
        that cannot be checked without knowing them.
        """
        def type_text(annotation) -> str:
            name = getattr(annotation, 'name', None)
            if name:
                return name
            bits = getattr(annotation, 'bits', None)
            if bits is not None:
                return f"{'int' if getattr(annotation, 'signed', False) else 'bit'}" \
                       f"[{bits}]"
            return type(annotation).__name__

        if not is_value:
            ret = 'bool'
        elif decl.getIs_return_numeric():
            # `numeric` has no concrete type until a reference reifies it, so it
            # is its own signature entry -- two `numeric` declarations match each
            # other, and neither matches an explicitly typed one.
            ret = 'numeric'
        else:
            ret = type_text(self._translate_data_type(ctx, decl.getReturn_type()))
        return (ret, tuple(type_text(p.annotation) for p in params))

    def _translate_generic_value_constraint(
        self,
        ctx: AstToIrContext,
        decl: 'pss_ast.GenericConstraintDeclValue',
    ) -> Optional[ir.Function]:
        """Translate ``constraint <type> name(params) expr;`` (§13.1.2 b).

        The value-yielding form is one *expression*, not a constraint set, and it
        is "usable anywhere an expression of that type is legal" -- so unlike the
        boolean form it never holds on its own; it only contributes a value to
        whatever constrains the reference. It is stored the same way regardless:
        an ``ir.Function`` whose single statement wraps the expression, which
        lets one instantiation path serve both forms (see
        :meth:`_instantiate_generic`). ``_is_generic_value`` is what tells the
        two apart where the difference matters -- a bare statement reference is
        legal for the boolean form and meaningless for this one.

        The declared return type is deliberately not recorded. Substituting the
        expression at the use site means each reference is typed by its own
        arguments and its own context, which is the specialization-per-signature
        behaviour §8.2 calls for -- recording one type here would invite
        reifying once and reusing it.
        """
        name_node = decl.getName()
        name = name_node.getId() if isinstance(name_node, pss_ast.ExprId) \
            else str(name_node)
        if not name:
            return None
        expr_node = decl.getExpr()
        if expr_node is None:
            return None

        params = self._translate_generic_constraint_params(ctx, decl)
        shadowed = {p.arg for p in params} - ctx.local_vars
        ctx.local_vars |= shadowed
        try:
            expr = self._translate_expression(ctx, expr_node)
        finally:
            ctx.local_vars -= shadowed
        if expr is None:
            return None

        return ir.Function(
            name=name,
            is_async=False,
            args=ir.Arguments(args=params),
            body=[ir.StmtExpr(expr=expr)],
            metadata={
                '_is_generic_constraint': True,
                '_is_generic_value': True,
                '_generic_const_params': self._const_param_names(decl),
                '_generic_signature': self._generic_signature(
                    ctx, decl, params, is_value=True),
            },
        )

    def _translate_enum(self, ctx: AstToIrContext, enum_decl: pss_ast.EnumDecl) -> ir.DataTypeEnum:
        """Translate a PSS enum declaration to IR DataTypeEnum.

        Assigns auto-incrementing values to items without an explicit value,
        following PSS §7.5 semantics (first item defaults to 0).

        Args:
            ctx: Translation context
            enum_decl: PSS EnumDecl AST node

        Returns:
            IR DataTypeEnum registered in ctx.type_map
        """
        name_node = enum_decl.getName()
        enum_name = name_node.getId() if hasattr(name_node, 'getId') else str(name_node)

        if self.debug:
            self.logger.debug(f"Translating enum: {enum_name}")

        items: dict = {}
        next_val = 0
        for i in range(enum_decl.numItems()):
            item = enum_decl.getItem(i)
            item_name_node = item.getName()
            item_name = item_name_node.getId() if hasattr(item_name_node, 'getId') else str(item_name_node)
            val_node = item.getValue()
            if val_node is not None and hasattr(val_node, 'getValue'):
                next_val = val_node.getValue()
            items[item_name] = next_val
            next_val += 1

        enum_ir = ir.DataTypeEnum(name=enum_name, items=items)
        ctx.add_type(enum_name, enum_ir)
        return enum_ir

    def _translate_typedef(self, ctx: AstToIrContext, typedef_decl: pss_ast.TypedefDeclaration) -> Optional[ir.DataType]:
        """Translate a PSS typedef declaration by registering a type alias.

        Args:
            ctx: Translation context
            typedef_decl: PSS TypedefDeclaration AST node

        Returns:
            The aliased IR DataType (also registered under the alias name)
        """
        name_node = typedef_decl.getName()
        alias_name = name_node.getId() if hasattr(name_node, 'getId') else str(name_node)

        if self.debug:
            self.logger.debug(f"Translating typedef: {alias_name}")

        base_type = self._translate_data_type(ctx, typedef_decl.getType())
        if base_type is None:
            ctx.add_error(f"Failed to translate type for typedef '{alias_name}'")
            return None

        ctx.add_type(alias_name, base_type)
        return base_type

    def _translate_field(self, ctx: AstToIrContext, field: pss_ast.Field) -> Optional[ir.Field]:
        """Translate a PSS field to IR Field

        Args:
            ctx: Translation context
            field: PSS field AST node

        Returns:
            IR Field or None
        """
        # Extract field name
        name_node = field.getName()
        if isinstance(name_node, pss_ast.ExprId):
            field_name = name_node.getId()
        else:
            field_name = str(name_node)

        if self.debug:
            self.logger.debug(f"Translating field: {field_name}")

        # Get field type
        field_type = self._translate_data_type(ctx, field.getType())
        if not field_type:
            ctx.add_error(f"Failed to translate field type for {field_name}")
            return None

        # Create IR field
        rand_kind = None
        attr = field.getAttr()
        if attr & pss_ast.FieldAttr.Rand:
            rand_kind = 'rand'

        # A field initializer (`bool ars = true;`, `int num_ch = MAX_CH;`) is
        # part of the field's meaning, not decoration: a capability struct whose
        # defaults are dropped reads as all-false, which silently disables every
        # operation gated on it. Carried on the field so a backend can emit it.
        init_node = field.getInit() if hasattr(field, 'getInit') else None
        initial_value = (self._translate_expression(ctx, init_node)
                         if init_node is not None else None)

        # Both slots, not `ast_doc`'s leading-else-trailing: a register value
        # struct's member routinely carries prose above it and its bit range
        # and access mode beside it, and the two are emitted in different
        # places. Collapsing them would drop whichever lost.
        field_doc, field_doc_trailing = ast_comments(field)

        ir_field = ir.Field(
            name=field_name,
            datatype=field_type,
            kind=ir.FieldKind.Field,
            rand_kind=rand_kind,
            initial_value=initial_value,
            doc=field_doc,
            doc_trailing=field_doc_trailing,
        )

        # Extract `rand int in [range]` domain constraint (T-12).
        dtype_node = field.getType()
        if (isinstance(dtype_node, pss_ast.DataTypeInt)
                and hasattr(dtype_node, 'getIn_range')
                and dtype_node.getIn_range() is not None):
            domain = dtype_node.getIn_range()
            range_list = self._translate_domain_range_list(ctx, domain)
            if range_list is not None:
                field_ref = ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=field_name)
                in_expr = ir.ExprIn(value=field_ref, container=range_list)
                # Carry the domain `in` constraint on the field itself; the owning
                # type flushes it via _flush_range_constraints. (Using a shared ctx
                # list previously leaked struct-level domains onto unrelated actions.)
                ir_field._pssc_domain_in = in_expr

        return ir_field

    def _translate_function(self, ctx: AstToIrContext, function) -> Optional[ir.Function]:
        """Translate a PSS function to IR Function

        Args:
            ctx: Translation context
            function: PSS FunctionDefinition AST node

        Returns:
            IR Function or None
        """
        # Get function prototype
        prototype = function.getProto()
        if not prototype:
            ctx.add_error("Function missing prototype")
            return None

        # Extract function name
        name_node = prototype.getName()
        if isinstance(name_node, pss_ast.ExprId):
            func_name = name_node.getId()
        else:
            func_name = str(name_node)

        if self.debug:
            self.logger.debug(f"Translating function: {func_name}")

        # Get return type (may be None for void)
        return_type_node = prototype.getRtype()
        return_type = None
        if return_type_node is not None:
            return_type = self._translate_data_type(ctx, return_type_node)

        # Get parameters
        params = []
        defaults = []
        vararg: Optional[ir.Arg] = None
        for i in range(prototype.numParameters()):
            param = prototype.getParameter(i)
            if param:
                # Extract param name and type
                param_name_node = param.getName()
                if isinstance(param_name_node, pss_ast.ExprId):
                    param_name = param_name_node.getId()
                else:
                    param_name = str(param_name_node)

                param_type = self._translate_data_type(ctx, param.getType())
                if param_type:
                    is_varargs = param.getIs_varargs() if hasattr(param, 'getIs_varargs') else False
                    arg_node = ir.Arg(arg=param_name, annotation=param_type)
                    if is_varargs:
                        vararg = arg_node
                    else:
                        params.append(arg_node)
                        dflt_node = param.getDflt() if hasattr(param, 'getDflt') else None
                        if dflt_node is not None:
                            dflt_ir = self._translate_expression(ctx, dflt_node)
                            if dflt_ir is not None:
                                defaults.append(dflt_ir)

        # Create Arguments structure
        args = ir.Arguments(args=params, vararg=vararg, defaults=defaults)

        # Translate function body
        body_stmts = []
        body = function.getBody()
        if body:
            saved_params = ctx.param_names
            ctx.param_names = {a.arg for a in params} | (
                {vararg.arg} if vararg is not None else set())
            try:
                body_stmts = self._translate_exec_scope(ctx, body)
            finally:
                ctx.param_names = saved_params

        # Create IR function
        is_pure = prototype.getIs_pure() if hasattr(prototype, 'getIs_pure') else False
        is_solve = bool(prototype.getIs_solve()) if hasattr(prototype, 'getIs_solve') else False
        is_target = bool(prototype.getIs_target()) if hasattr(prototype, 'getIs_target') else False
        ir_func = ir.Function(
            name=func_name,
            args=args,
            body=body_stmts,
            returns=return_type,
            is_async=False,
            is_invariant=bool(is_pure),
            is_solve=is_solve,
            is_target=is_target,
            doc=ast_doc(function),
        )

        return ir_func

    def _translate_exec_scope(self, ctx: AstToIrContext, exec_scope) -> List[ir.Stmt]:
        """Translate an execution scope (function body)

        Args:
            ctx: Translation context
            exec_scope: PSS ExecScope node

        Returns:
            List of IR statements
        """
        with self._local_scope(ctx):
            return self._translate_block_children(ctx, exec_scope)

    @contextlib.contextmanager
    def _local_scope(self, ctx: AstToIrContext):
        """Locals declared inside end with the scope (20.7.1, 20.7.2)."""
        saved_vars, saved_renames = set(ctx.local_vars), dict(ctx.local_renames)
        try:
            yield
        finally:
            ctx.local_vars, ctx.local_renames = saved_vars, saved_renames

    def _bind_local(self, ctx: AstToIrContext, name: str) -> str:
        """Declare the local *name* in the current scope; its IR name.

        A local that shadows a visible local or a parameter gets a fresh IR name
        (20.7.1): a nested block is flattened into its parent's statement list,
        and a loop's variables are scoped to the loop, so a backend whose
        language scopes names to the function would otherwise overwrite the
        outer one. Every declaration form binds through here -- a data
        declaration, a `repeat` index, `foreach` iterator and index variables.
        Call it inside `_local_scope` so the binding ends with its scope.
        """
        ir_name = name
        if name in ctx.local_vars or name in ctx.param_names:
            ctx.local_rename_seq += 1
            ir_name = f"{name}__s{ctx.local_rename_seq}"
            ctx.local_renames[name] = ir_name
        ctx.local_vars.add(name)
        return ir_name

    def _translate_block_children(self, ctx: AstToIrContext, scope) -> List[ir.Stmt]:
        """The statements of a block, with any nested stand-alone block flattened.

        A stand-alone ``{ ... }`` (20.7.1) has no IR node of its own; its
        statements join the enclosing list, and a declaration in it that shadows
        an outer local is renamed for the block's extent. Until this existed a
        nested block reached ``_translate_statement_kind``, matched nothing, and
        was dropped with every statement in it.
        """
        stmts: List[ir.Stmt] = []
        for child in scope.children():
            if child is None:
                continue
            if isinstance(child, pss_ast.ExecScope):
                with self._local_scope(ctx):
                    stmts.extend(self._translate_block_children(ctx, child))
                continue
            stmt = self._translate_statement(ctx, child)
            if stmt:
                stmts.append(stmt)
        return stmts

    def _translate_statement(self, ctx: AstToIrContext, stmt_node: Any) -> Optional[ir.Stmt]:
        """Translate a statement node to IR, carrying its comments across.

        Every procedural statement at every nesting level reaches the IR
        through this one method, so stamping here covers a comment inside a
        nested `if` body as well as one at the top of a function.
        """
        stmt = self._translate_statement_kind(ctx, stmt_node)
        if stmt is None:
            # A statement with no IR form is REFUSED, never dropped: a dropped
            # one compiles a model that does less than it says (`super;` was
            # lost this way, and a call whose expression did not translate).
            ctx.errors.append(
                f"{_ast_where(stmt_node)}a '{type(stmt_node).__name__}' "
                f"statement could not be translated; refusing it rather than "
                f"dropping it")
            return None

        if stmt is not None:
            leading, trailing = ast_comments(stmt_node)
            if leading is not None:
                stmt.comment = leading
            if trailing is not None:
                stmt.comment_trailing = trailing

        return stmt

    def _translate_statement_kind(self, ctx: AstToIrContext, stmt_node: Any) -> Optional[ir.Stmt]:
        """Dispatch on the statement kind.

        Args:
            ctx: Translation context
            stmt_node: PSS statement AST node

        Returns:
            IR statement or None
        """
        if isinstance(stmt_node, pss_ast.ProceduralStmtReturn):
            return self._translate_stmt_return(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtDataDeclaration):
            return self._translate_stmt_declaration(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtAssignment):
            return self._translate_stmt_assignment(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtIfElse):
            return self._translate_stmt_if(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtWhile):
            return self._translate_stmt_while(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtRepeat):
            return self._translate_stmt_repeat(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtRepeatWhile):
            return self._translate_stmt_repeat_while(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtBreak):
            return ir.StmtBreak()
        elif isinstance(stmt_node, pss_ast.ProceduralStmtContinue):
            return ir.StmtContinue()
        elif isinstance(stmt_node, pss_ast.ProceduralStmtExpr):
            # A bare call statement (`f();`, `c.f();`) arrives here, not under a
            # node of its own -- the parser has no ProceduralStmtFunctionCall.
            expr_node = stmt_node.getExpr()
            if expr_node is not None:
                ir_expr = self._translate_expression(ctx, expr_node)
                if ir_expr is not None:
                    return ir.StmtExpr(expr=ir_expr)
            return None
        elif isinstance(stmt_node, pss_ast.ProceduralStmtYield):
            return ir.StmtYield()
        elif isinstance(stmt_node, pss_ast.ProceduralStmtForeach):
            return self._translate_stmt_foreach(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtMatch):
            return self._translate_stmt_match(ctx, stmt_node)
        elif isinstance(stmt_node, pss_ast.ProceduralStmtSuper):
            # Which base block it runs is fixed by where it sits (17.1); the
            # consumer that knows the enclosing type resolves it.
            return ir.StmtSuper()
        else:
            return None

    def _translate_stmt_return(self, ctx: AstToIrContext, stmt: pss_ast.ProceduralStmtReturn) -> ir.StmtReturn:
        """Translate a return statement"""
        expr_node = stmt.getExpr()
        value = None
        if expr_node:
            value = self._translate_expression(ctx, expr_node)
        return ir.StmtReturn(value=value)

    def _translate_stmt_declaration(self, ctx: AstToIrContext, stmt: pss_ast.ProceduralStmtDataDeclaration) -> ir.StmtAnnAssign:
        """Translate a variable declaration statement"""
        # Get variable name
        name_node = stmt.getName()
        if isinstance(name_node, pss_ast.ExprId):
            var_name = name_node.getId()
        else:
            var_name = str(name_node)

        # Get variable type
        var_type = self._translate_data_type(ctx, stmt.getDatatype())

        # Get initial value (if any)
        init_expr = stmt.getInit()
        value = None
        if init_expr:
            value = self._translate_expression(ctx, init_expr)

        # Create name expression for target and register as local variable. A
        # declaration that shadows a visible local gets a fresh IR name.
        target = ir.ExprRefLocal(name=self._bind_local(ctx, var_name))

        return ir.StmtAnnAssign(target=target, annotation=var_type, value=value)

    def _translate_stmt_assignment(self, ctx: AstToIrContext, stmt: pss_ast.ProceduralStmtAssignment):
        """Translate an assignment or compound-assignment statement."""
        lhs = stmt.getLhs()
        target = self._translate_expression(ctx, lhs)
        rhs = stmt.getRhs()
        value = self._translate_expression(ctx, rhs)

        op = stmt.getOp()
        _aug_op_map = {
            pss_ast.AssignOp.AssignOp_PlusEq:  ir.AugOp.Add,
            pss_ast.AssignOp.AssignOp_MinusEq: ir.AugOp.Sub,
            pss_ast.AssignOp.AssignOp_ShlEq:   ir.AugOp.LShift,
            pss_ast.AssignOp.AssignOp_ShrEq:   ir.AugOp.RShift,
            pss_ast.AssignOp.AssignOp_OrEq:    ir.AugOp.BitOr,
            pss_ast.AssignOp.AssignOp_AndEq:   ir.AugOp.BitAnd,
        }
        if op in _aug_op_map:
            return ir.StmtAugAssign(target=target, op=_aug_op_map[op], value=value)
        return ir.StmtAssign(targets=[target], value=value)

    def _translate_stmt_if(self, ctx: AstToIrContext, stmt: pss_ast.ProceduralStmtIfElse) -> ir.StmtIf:
        """Translate an if / else-if / else chain into nested StmtIf nodes."""
        def _translate_body(scope):
            return self._translate_stmt_body(ctx, scope)

        # Build the final else branch first
        else_body = _translate_body(stmt.getElse_then())

        # Walk if-then clauses from last to first, nesting each into the else of the previous
        num_clauses = stmt.numIf_then()
        result = else_body
        for i in range(num_clauses - 1, -1, -1):
            clause = stmt.getIf_then(i)
            cond = self._translate_expression(ctx, clause.getCond())
            then_body = _translate_body(clause.getBody())
            result = [ir.StmtIf(test=cond, body=then_body, orelse=result)]

        return result[0] if result else ir.StmtPass()

    def _translate_stmt_while(self, ctx: AstToIrContext, stmt) -> ir.StmtWhile:
        """Translate a while loop"""
        # Get condition
        cond = self._translate_expression(ctx, stmt.getExpr())

        # Get body
        body_scope = stmt.getBody()
        body = self._translate_stmt_body(ctx, body_scope)

        return ir.StmtWhile(test=cond, body=body)

    def _translate_stmt_repeat(self, ctx: AstToIrContext, stmt) -> ir.StmtFor:
        """Translate a repeat statement (PSS for loop)"""
        # Get count expression
        count_expr = self._translate_expression(ctx, stmt.getCount())

        # Optional index variable: "repeat (i : 10)" has getIt_id() == "i". It
        # is scoped to the loop (20.7.6), so it is bound in a scope of its own
        # and may shadow an outer local, which keeps its value after the loop.
        it_id = stmt.getIt_id()
        target = None
        with self._local_scope(ctx):
            if it_id is not None:
                iter_name = it_id.getId() if hasattr(it_id, 'getId') else str(it_id)
                target = ir.ExprRefLocal(name=self._bind_local(ctx, iter_name))
            body = self._translate_stmt_body(ctx, stmt.getBody())

        return ir.StmtFor(target=target, iter=count_expr, body=body)

    def _translate_stmt_repeat_while(self, ctx: AstToIrContext, stmt) -> ir.StmtRepeatWhile:
        """Translate a repeat-while statement (PSS do-while: body executes at least once)."""
        cond = self._translate_expression(ctx, stmt.getExpr())

        body_scope = stmt.getBody()
        body = self._translate_stmt_body(ctx, body_scope)

        return ir.StmtRepeatWhile(condition=cond, body=body)

    def _translate_stmt_foreach(self, ctx: AstToIrContext, stmt: pss_ast.ProceduralStmtForeach) -> Optional[ir.StmtForeach]:
        """Translate a foreach statement: foreach (e : items) { ... }"""
        it_id = stmt.getIt_id()
        idx_id = stmt.getIdx_id()
        path = stmt.getPath()

        if path is None or (it_id is None and idx_id is None):
            if self.debug:
                self.logger.debug("foreach: missing iterator/index or path")
            return None

        def _name(n):
            if n is None:
                return None
            return n.getId() if hasattr(n, 'getId') else str(n)

        it_name = _name(it_id)
        idx_name = _name(idx_id)

        # The loop "target" is the variable the body uses to walk the collection.
        # Element form `foreach(v : arr)` binds `v` to the element; index form
        # `foreach(arr[i])` binds `i` to the index (no element var). The C lowering
        # unrolls by index keying off ``target.name``, so for the index-only form
        # the index variable serves as the target.
        iter_name = it_name if it_name is not None else idx_name

        collection_ir = self._translate_expression(ctx, path)
        if collection_ir is None:
            return None

        # The loop variables are scoped to the loop (20.7.8 e), so body
        # references resolve to them and an outer local of the same name is
        # untouched after it.
        with self._local_scope(ctx):
            target = ir.ExprRefLocal(name=self._bind_local(ctx, iter_name))
            index_var = None
            if idx_name is not None:
                index_var = (target if idx_name == iter_name else
                             ir.ExprRefLocal(name=self._bind_local(ctx, idx_name)))
            body = self._translate_stmt_body(ctx, stmt.getBody())

        return ir.StmtForeach(target=target, iter=collection_ir, body=body, index_var=index_var)

    def _translate_stmt_body(self, ctx: AstToIrContext, node) -> List[ir.Stmt]:
        """Translate a statement body that may be either a block (a scope with
        ``children()``) or a single bare statement.

        PSS allows a single statement after a ``match``/``if`` arm, e.g.
        ``["CSR"]: return 0x00;`` (as the register offset functions use), as well
        as a ``{ ... }`` compound. The block case iterates children; the bare
        case translates the node directly.
        """
        body: List[ir.Stmt] = []
        if node is None:
            return body
        # Only a `{ ... }` block is a scope to iterate. Some statement nodes
        # (`repeat`, `foreach`) also expose children(), so duck-typing on it
        # would translate an unbraced `if (c) repeat (n) {...}` as the repeat's
        # innards rather than as the repeat.
        with self._local_scope(ctx):
            if isinstance(node, pss_ast.ExecScope):
                return self._translate_block_children(ctx, node)
            stmt_ir = self._translate_statement(ctx, node)
            if stmt_ir:
                body.append(stmt_ir)
        return body

    def _translate_match_pattern(self, ctx: AstToIrContext, cond_node):
        """Translate a match-arm condition (an ``ExprOpenRangeList`` ``[...]``) to
        an IR pattern.

        Each open-range value is a single value (``[x]``) or a range
        (``[lo..hi]``). A single value -> ``PatternValue``; a range ->
        ``PatternValue(ExprRange(lo, hi))``; several in one arm -> ``PatternOr``.

        A range used to key off its low bound alone, so ``[0..3]`` matched only
        0 -- a silent miscompile of every ranged arm (20.7.10). A backend that
        cannot render a range now meets an ``ExprRange`` it must reject.
        """
        if cond_node is None:
            return ir.PatternAs(pattern=None, name="_")

        pats = []
        if hasattr(cond_node, "numValues"):
            for i in range(cond_node.numValues()):
                orv = cond_node.getValue(i)
                lhs = orv.getLhs() if hasattr(orv, "getLhs") else None
                rhs = orv.getRhs() if hasattr(orv, "getRhs") else None
                lhs_ir = self._translate_expression(ctx, lhs) if lhs is not None else None
                rhs_ir = self._translate_expression(ctx, rhs) if rhs is not None else None
                if lhs_ir is not None and rhs_ir is not None:
                    pats.append(ir.PatternValue(
                        value=ir.ExprRange(lower=lhs_ir, upper=rhs_ir)))
                elif lhs_ir is not None:
                    pats.append(ir.PatternValue(value=lhs_ir))
        else:
            e = self._translate_expression(ctx, cond_node)
            if e is not None:
                pats.append(ir.PatternValue(value=e))

        if not pats:
            return ir.PatternAs(pattern=None, name="_")
        if len(pats) == 1:
            return pats[0]
        return ir.PatternOr(patterns=pats)

    def _translate_stmt_match(self, ctx: AstToIrContext, stmt: pss_ast.ProceduralStmtMatch) -> Optional[ir.StmtMatch]:
        """Translate a match statement: match (expr) { [val]: { ... } default: { ... } }"""
        subject_node = stmt.getExpr()
        if subject_node is None:
            return None
        subject = self._translate_expression(ctx, subject_node)
        if subject is None:
            return None

        cases = []
        for i in range(stmt.numChoices()):
            choice = stmt.getChoice(i)
            if choice is None:
                continue
            body = self._translate_stmt_body(ctx, choice.getBody())

            if choice.getIs_default():
                pattern = ir.PatternAs(pattern=None, name="_")
            else:
                pattern = self._translate_match_pattern(ctx, choice.getCond())

            cases.append(ir.StmtMatchCase(pattern=pattern, body=body))

        return ir.StmtMatch(subject=subject, cases=cases)

    def _translate_expression(self, ctx: AstToIrContext, expr_node: Any) -> Optional[ir.Expr]:
        """Translate an expression node to IR

        Args:
            ctx: Translation context
            expr_node: PSS expression AST node

        Returns:
            IR expression or None
        """
        if isinstance(expr_node, (pss_ast.ExprNumber, pss_ast.ExprSignedNumber, pss_ast.ExprUnsignedNumber)):
            return self._translate_expr_number(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprString):
            return self._translate_expr_string(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprBool):
            return self._translate_expr_bool(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprBin):
            return self._translate_expr_bin(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprUnary):
            return self._translate_expr_unary(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprCond):
            return self._translate_expr_cond(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprRefPathContext):
            return self._translate_expr_ref(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprCast):
            return self._translate_expr_cast(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprBitSlice):
            return self._translate_expr_bitslice(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprSliceRange):
            return self._translate_expr_slice_range(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprAggrList):
            return self._translate_expr_aggr_list(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprAggrMap):
            return self._translate_expr_aggr_map(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprAggrStruct):
            return self._translate_expr_aggr_struct(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprAggrEmpty):
            return ir.ExprList(elts=[])
        elif isinstance(expr_node, pss_ast.ExprNull):
            return ir.ExprNull()
        elif isinstance(expr_node, pss_ast.ExprIn):
            return self._translate_expr_in(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprRefPathStaticRooted):
            return self._translate_expr_ref_static_rooted(ctx, expr_node)
        elif isinstance(expr_node, pss_ast.ExprRefPathStatic):
            return self._translate_expr_ref_static(ctx, expr_node)
        else:
            if self.debug:
                self.logger.debug(f"Unsupported expression type: {type(expr_node).__name__}")
            return None

    def _translate_expr_ref_static(self, ctx: AstToIrContext, expr) -> Optional[ir.Expr]:
        """Translate a qualified static reference: ``pkg::NAME``.

        This had no branch at all, so every such reference translated to
        ``None`` -- and a ``None`` inside an expression tree is not an error
        anywhere, it is just an operand that quietly disappears. The address
        arithmetic in the WB DMA model's `\\init`,

            make_handle_from_handle(base, WB_DMA_CH_BASE + i * WB_DMA_CH_STRIDE)

        reached the IR as ``(None + (i * None))``.

        Constants (package-scope `static const`, enum items) fold to literals;
        anything else becomes an attribute path, which is at least a name a
        backend can report on.
        """
        parts: List[str] = []
        for i in range(expr.numBase()):
            elem = expr.getBase(i)
            eid = elem.getId() if hasattr(elem, 'getId') else None
            if eid is not None and hasattr(eid, 'getId'):
                parts.append(eid.getId())
        if not parts:
            return None

        qualified = "::".join(parts)
        for key in (qualified, parts[-1]):
            if key in ctx.const_map:
                return ir.ExprConstant(value=ctx.const_map[key])
        enum_val = self._resolve_enum_constant(ctx, parts[-1])
        if enum_val is not None:
            return ir.ExprConstant(value=enum_val)

        if self.debug:
            self.logger.debug(f"static reference not folded: {qualified}")
        result: ir.Expr = ir.TypeExprRefSelf()
        for name in parts:
            result = ir.ExprAttribute(value=result, attr=name)
        return result

    def _translate_expr_ref_static_rooted(self, ctx: AstToIrContext,
                                          expr) -> Optional[ir.Expr]:
        """Translate ``pkg::name(args)`` -- a static path with a call on its leaf.

        This had no branch, so a reference to a package-scope generic constraint
        translated to ``None``, and a ``None`` constraint statement is not an
        error anywhere: it is simply absent. ``constraint c { p::lt(x, 20); }``
        therefore compiled to a model with *no* upper bound on ``x`` at all --
        stimulus silently weaker than the source asks for, which is the worst
        failure mode a constraint compiler has.

        Deliberately narrow: only a reference that names a *known generic
        constraint* is translated, and everything else keeps returning ``None``.
        The same node spells package-scope function calls and enum references,
        which have their own unfinished handling; turning those into calls on an
        unresolved name here would trade one silent gap for a noisier one
        elsewhere without fixing either.
        """
        root = expr.getRoot()
        leaf = expr.getLeaf()
        if root is None or leaf is None:
            return None

        parts: List[str] = []
        for i in range(root.numBase()):
            elem = root.getBase(i)
            eid = elem.getId() if hasattr(elem, 'getId') else None
            if eid is not None and hasattr(eid, 'getId'):
                parts.append(eid.getId())

        args_node = None
        for i in range(leaf.numElems()):
            elem = leaf.getElem(i)
            eid = elem.getId() if hasattr(elem, 'getId') else None
            if eid is None:
                return None
            parts.append(eid.getId())
            args_node = elem.getParams()

        if args_node is None or not parts:
            return None

        qualified = "::".join(parts)
        # A generic constraint, or a package-scope function called by its
        # qualified name (`util::neg(x)`) -- which one the LINKER says: the
        # root path resolves to the package and the leaf's target is the
        # function's index in it.
        if qualified in ctx.generic_constraints:
            what = "generic constraint"
        else:
            rt = root.getTarget()
            fname = None
            if rt is not None and leaf.numElems() == 1:
                # `bodyless`: a qualified call to a core-library function
                # (`addr_reg_pkg::write32(h, d)`, the same function as
                # `write32(h, d)` and delegated like it, LRM 21.13.9.5) or to
                # an import. Neither has a body to lower; the name says
                # which function, and the backend's registry decides.
                fname = self._package_function_at(
                    ctx, [pe.idx for pe in rt.getPathList()]
                    + [leaf.getElem(0).getTarget()], bodyless=True)
            if fname is None:
                return None
            qualified, what = fname, "function"

        args: List[ir.Expr] = []
        for arg_node in args_node.getParameters():
            arg_ir = self._translate_expression(ctx, arg_node)
            if arg_ir is None:
                # Dropping an argument would silently change the signature, and
                # the arity check downstream would then blame the declaration.
                ctx.errors.append(
                    f"could not translate an argument to {what} "
                    f"'{qualified}'")
                return None
            args.append(arg_ir)

        return ir.ExprCall(func=ir.ExprRefUnresolved(name=qualified), args=args)

    def _translate_expr_number(self, ctx: AstToIrContext, expr: Any) -> ir.ExprConstant:
        """Translate a number literal"""
        value = expr.getValue()
        return ir.ExprConstant(value=value)

    def _translate_expr_string(self, ctx: AstToIrContext, expr: pss_ast.ExprString) -> ir.ExprConstant:
        """Translate a string literal"""
        value = expr.getValue()
        return ir.ExprConstant(value=value)

    def _translate_expr_bool(self, ctx: AstToIrContext, expr: pss_ast.ExprBool) -> ir.ExprConstant:
        """Translate a boolean literal"""
        value = expr.getValue()
        return ir.ExprConstant(value=value)

    def _translate_expr_bin(self, ctx: AstToIrContext, expr) -> ir.ExprBin:
        """Translate a binary expression"""
        # Get left and right operands
        lhs = self._translate_expression(ctx, expr.getLhs())
        rhs = self._translate_expression(ctx, expr.getRhs())

        # Get operator
        op = self._map_binop(ctx, expr.getOp())

        return ir.ExprBin(lhs=lhs, op=op, rhs=rhs)

    def _translate_expr_unary(self, ctx: AstToIrContext, expr: pss_ast.ExprUnary) -> ir.ExprUnary:
        """Translate a unary expression"""
        # Get operand
        operand = self._translate_expression(ctx, expr.getRhs())

        # Get operator
        op = self._map_unaryop(ctx, expr.getOp())

        return ir.ExprUnary(op=op, operand=operand)

    def _translate_expr_cond(self, ctx: AstToIrContext, expr) -> ir.ExprIfExp:
        """Translate a conditional (ternary) expression"""
        test = self._translate_expression(ctx, expr.getCond_e())
        body = self._translate_expression(ctx, expr.getTrue_e())
        orelse = self._translate_expression(ctx, expr.getFalse_e())

        return ir.ExprIfExp(test=test, body=body, orelse=orelse)

    def _translate_expr_in(self, ctx: AstToIrContext, expr) -> ir.ExprIn:
        """Translate a PSS 'in' expression to IR ExprIn.

        Two forms:
          Range-list: x in [0, 1, 2]     -> ExprIn(value, ExprRangeList([...]))
          Collection: x in comp.some_list -> ExprIn(value, <ExprAttribute chain>)

        The collection form is detected via getCollection() (non-None when the
        grammar matched collection_expression rather than open_range_list).
        """
        value = self._translate_expression(ctx, expr.getLhs())

        # Collection-reference form: x in comp.some_list
        coll_node = expr.getCollection() if hasattr(expr, 'getCollection') else None
        if coll_node is not None:
            container = self._translate_expression(ctx, coll_node)
            return ir.ExprIn(value=value, container=container)

        # Range-list form: x in [0, 1, 2] or x in [lo..hi]
        rhs = expr.getRhs()  # ExprOpenRangeList
        ranges = []
        if rhs is not None:
            for i in range(rhs.numValues()):
                v = rhs.getValue(i)
                lower = self._translate_expression(ctx, v.getLhs()) if v.getLhs() else None
                upper = self._translate_expression(ctx, v.getRhs()) if v.getRhs() else None
                ranges.append(ir.ExprRange(lower=lower, upper=upper))
        return ir.ExprIn(value=value, container=ir.ExprRangeList(ranges=ranges))

    @staticmethod
    def _package_function_at(ctx: AstToIrContext, idxs,
                             bodyless: bool = False) -> Optional[str]:
        """The qualified name of the package-scope function at ``idxs``, or
        None.

        ``idxs`` is a linker resolution: child indices from the root symbol
        scope. It names a package-scope function when every step before the
        last is a package and the last is a function WITH A BODY -- a
        `std_pkg` built-in and an `import` function have none, and keep their
        own handling. The name is built from the scopes walked, so it is the
        key `_record_scope_function` files the function under.

        ``bodyless`` accepts a function without a body too. Only the QUALIFIED
        call form asks for it: an unqualified built-in keeps the `self.<name>`
        form every backend recognises, and a qualified one had no form at all
        -- its statement was dropped.
        """
        scope = getattr(ctx, "symbol_root", None)
        if scope is None or not idxs:
            return None
        names: List[str] = []
        for k, i in enumerate(idxs):
            if i is None or i < 0 or i >= scope.numChildren():
                return None
            scope = scope.getChild(i)
            if k < len(idxs) - 1:
                # Only a package on the way: through a type, it is a member.
                if type(scope) is not pss_ast.SymbolScope:
                    return None
                names.append(scope.getName())
        if not isinstance(scope, pss_ast.SymbolFunctionScope):
            return None
        if scope.getBody() is None and not bodyless:
            return None
        names.append(scope.getName())
        return "::".join(names)

    def _translate_expr_ref(self, ctx: AstToIrContext, expr) -> ir.Expr:
        """Translate a reference expression (variable, field, or method call) to IR.

        For plain references like `a.b.c`, builds an ExprAttribute chain:
            self.a.b.c

        For method calls like `a.b.method(x, y)`, the final element has a
        MethodParameterList and is emitted as ExprCall:
            ExprCall(func=self.a.b.method, args=[x, y])
        """
        hier_id = expr.getHier_id()
        if not hier_id or hier_id.numElems() == 0:
            return ir.ExprRefUnresolved(name="unknown")

        elems = [hier_id.getElem(i) for i in range(hier_id.numElems())]

        # `super.f(...)` / `super.x`: rooted at the base type, not at `self`.
        # The front end builds an ExprRefPathSuper for exactly these, so the
        # form is decided here, before any branch below can read the name as a
        # local, a constant or a package function. Translating it as `self.f`
        # made an override calling its base call itself.
        if isinstance(expr, pss_ast.ExprRefPathSuper):
            return self._translate_ref_chain(ctx, expr, elems,
                                             ir.TypeExprRefSuper())

        # Check if the first element is a known local variable (e.g. foreach iterator 'p').
        # Single-element: return ExprRefLocal('p').
        # Multi-element: build ExprAttribute(ExprRefLocal('p'), 'x', ...) instead of
        #   ExprAttribute(TypeExprRefSelf(), 'p', 'x') so the solver can substitute 'p'.
        if elems and hasattr(elems[0], 'getId') and ctx is not None:
            first_id = elems[0].getId()
            first_name = first_id.getId() if isinstance(first_id, pss_ast.ExprId) else str(first_id)
            if first_name in ctx.local_vars:
                local_name = ctx.local_renames.get(first_name, first_name)
                if len(elems) == 1:
                    return self._apply_subscripts(
                        ctx, elems[0], ir.ExprRefLocal(name=local_name))
                # Multi-element path rooted at a local variable (e.g. p.x, s.upper())
                result_lv: ir.Expr = self._apply_subscripts(
                    ctx, elems[0], ir.ExprRefLocal(name=local_name))
                for elem in elems[1:]:
                    if not hasattr(elem, 'getId'):
                        continue
                    id_obj = elem.getId()
                    attr = id_obj.getId() if isinstance(id_obj, pss_ast.ExprId) else str(id_obj)
                    result_lv = ir.ExprAttribute(value=result_lv, attr=attr)
                    result_lv = self._apply_subscripts(ctx, elem, result_lv)
                    # Handle method calls on this element (e.g. s.upper())
                    params = elem.getParams() if hasattr(elem, 'getParams') else None
                    if params is not None and hasattr(params, 'numParameters'):
                        args = []
                        for j in range(params.numParameters()):
                            arg_node = params.getParameter(j)
                            arg_ir = self._translate_expression(ctx, arg_node)
                            if arg_ir is not None:
                                args.append(arg_ir)
                        result_lv = ir.ExprCall(func=result_lv, args=args)
                return result_lv

        # A call the linker resolved to a package-scope function: a function
        # named by its qualified name, not a member of `self`. Deciding this
        # from the resolution is what makes a component's own `twice` shadow
        # the package's inside the component, and not inside a package
        # function -- no backend has to re-derive it from the name.
        if (len(elems) == 1 and ctx is not None
                and getattr(elems[0], 'getParams', lambda: None)() is not None):
            ref = expr.getTarget() if hasattr(expr, 'getTarget') else None
            qname = (self._package_function_at(
                        ctx, [pe.idx for pe in ref.getPathList()])
                     if ref is not None else None)
            if qname is not None:
                params = elems[0].getParams()
                args: List[ir.Expr] = []
                for j in range(params.numParameters()):
                    arg_ir = self._translate_expression(ctx, params.getParameter(j))
                    if arg_ir is None:
                        ctx.errors.append(
                            f"could not translate an argument to function "
                            f"'{qname}'")
                        return ir.ExprRefUnresolved(name=qname)
                    args.append(arg_ir)
                return self._apply_subscripts(
                    ctx, elems[0],
                    ir.ExprCall(func=ir.ExprRefUnresolved(name=qname), args=args))

        # A single-element reference may name a constant rather than a field:
        # an enum item, or a package-scope `static const` imported by name.
        # Folding it here is what lets an address expression written in terms of
        # the map constants become arithmetic a backend can emit.
        if len(elems) == 1 and hasattr(elems[0], 'getId') and ctx is not None:
            id_obj = elems[0].getId()
            name = id_obj.getId() if isinstance(id_obj, pss_ast.ExprId) else str(id_obj)
            enum_val = self._resolve_enum_constant(ctx, name)
            if enum_val is not None:
                return ir.ExprConstant(value=enum_val)
            if name in ctx.const_map and name not in ctx.local_vars:
                return ir.ExprConstant(value=ctx.const_map[name])

        # Build the ExprAttribute chain starting from self. (`super.x` was
        # handled above: the front end gives it its own node, ExprRefPathSuper.)
        return self._translate_ref_chain(ctx, expr, elems, ir.TypeExprRefSelf())

    def _translate_ref_chain(self, ctx: AstToIrContext, expr, elems,
                             root: ir.Expr) -> ir.Expr:
        """`a.b[i].f(x)` as an ExprAttribute chain from *root*, with a call
        wherever an element carries parameters."""
        result: ir.Expr = root
        for elem in elems:
            if not hasattr(elem, 'getId'):
                continue
            id_obj = elem.getId()
            name = id_obj.getId() if isinstance(id_obj, pss_ast.ExprId) else str(id_obj)
            result = ir.ExprAttribute(value=result, attr=name)

            # Apply any subscript indexes on this element: items[0] → result[0]
            result = self._apply_subscripts(ctx, elem, result)

            # If this element has method parameters, emit a call immediately
            params = elem.getParams() if hasattr(elem, 'getParams') else None
            if params is not None and hasattr(params, 'numParameters'):
                args = []
                for j in range(params.numParameters()):
                    arg_node = params.getParameter(j)
                    arg_ir = self._translate_expression(ctx, arg_node)
                    if arg_ir is not None:
                        args.append(arg_ir)
                result = ir.ExprCall(func=result, args=args)

        return self._apply_ref_bit_slice(expr, result)

    def _apply_subscripts(self, ctx: AstToIrContext, elem, result: ir.Expr) -> ir.Expr:
        """Wrap *result* in an ``ExprSubscript`` per subscript carried by *elem*.

        The parser hangs subscripts off the hier-id element that wrote them, so
        ``items[0]`` and ``s[1..3]`` both arrive here: a plain index translates to
        an index expression, a range to an ``ExprSlice`` (see
        :meth:`_translate_expr_slice_range`).
        """
        n_sub = elem.numSubscript() if hasattr(elem, 'numSubscript') else 0
        for si in range(n_sub):
            sub_expr = elem.getSubscript(si)
            if sub_expr is None:
                continue
            index_ir = self._translate_expression(ctx, sub_expr)
            if index_ir is not None:
                result = ir.ExprSubscript(value=result, slice=index_ir)
        return result

    def _apply_ref_bit_slice(self, expr_node, result: ir.Expr) -> ir.Expr:
        """If *expr_node* carries a bit-slice, wrap *result* in ExprSubscript.

        ``ExprRefPathContext`` and the static variants can all have a
        ``getSlice()`` method returning an ``ExprBitSlice`` AST node.  When
        present, we must produce ``ExprSubscript(value=result, slice=ExprSlice(...)``
        so the SV lowering emits ``result[upper:lower]`` in the constraint.
        """
        if not hasattr(expr_node, 'getSlice'):
            return result
        slice_node = expr_node.getSlice()
        if slice_node is None:
            return result
        # bit_slice grammar: [upper:lower]; ExprBitSlice has getLhs()=upper, getRhs()=lower
        upper_node = slice_node.getLhs() if hasattr(slice_node, 'getLhs') else None
        lower_node = slice_node.getRhs() if hasattr(slice_node, 'getRhs') else None
        # Translate bounds as plain numeric constants (they are always constant exprs)
        upper_expr = ir.ExprConstant(value=int(upper_node.getValue())) if (upper_node and hasattr(upper_node, 'getValue')) else None
        lower_expr = ir.ExprConstant(value=int(lower_node.getValue())) if (lower_node and hasattr(lower_node, 'getValue')) else None
        slc = ir.ExprSlice(lower=lower_expr, upper=upper_expr, step=None, is_bit_slice=True)
        return ir.ExprSubscript(value=result, slice=slc)

    def _resolve_enum_constant(self, ctx: AstToIrContext, name: str) -> Optional[int]:
        """Check if *name* is an enum member across all registered enums.

        Returns the integer value if found, or None.
        """
        for dt in ctx.type_map.values():
            if isinstance(dt, ir.DataTypeEnum) and name in dt.items:
                return dt.items[name]
        return None

    def _translate_expr_cast(self, ctx: AstToIrContext, expr) -> ir.ExprCast:
        """Translate a cast expression.

        Handles both numeric casts ``(bit[N])val`` and enum casts ``(my_enum)val``.
        """
        target_type = self._translate_data_type(ctx, expr.getCasting_type())
        operand = self._translate_expression(ctx, expr.getExpr())

        return ir.ExprCast(target_type=target_type, value=operand)

    def _translate_expr_bitslice(self, ctx: AstToIrContext, expr: pss_ast.ExprBitSlice) -> ir.ExprSlice:
        """Translate a standalone bit-slice expression.

        ExprBitSlice only carries the bounds (getLhs()=upper, getRhs()=lower).
        When a bit-slice appears on a ref-path it is handled via _apply_ref_bit_slice;
        this path handles any rare standalone occurrence.
        """
        upper_node = expr.getLhs()
        lower_node = expr.getRhs()
        upper = ir.ExprConstant(value=int(upper_node.getValue())) if (upper_node and hasattr(upper_node, 'getValue')) else None
        lower = ir.ExprConstant(value=int(lower_node.getValue())) if (lower_node and hasattr(lower_node, 'getValue')) else None
        return ir.ExprSlice(lower=lower, upper=upper, step=None, is_bit_slice=True)

    def _translate_expr_slice_range(self, ctx: AstToIrContext, expr: pss_ast.ExprSliceRange) -> ir.ExprSlice:
        """Translate a range subscript ``a[lower..upper]`` to an ``ExprSlice``.

        The parser attaches range subscripts to the hier-id element that carries
        them, so this node arrives as the *index* of a subscript: the caller in
        :meth:`_translate_expr_ref` wraps the result in
        ``ExprSubscript(value=..., slice=ExprSlice(...))``.

        Either endpoint may be absent (``a[lo..]`` / ``a[..hi]``), in which case
        the corresponding bound is ``None``.
        """
        lower_node = expr.getLower()
        upper_node = expr.getUpper()
        lower = self._translate_expression(ctx, lower_node) if lower_node is not None else None
        upper = self._translate_expression(ctx, upper_node) if upper_node is not None else None
        return ir.ExprSlice(lower=lower, upper=upper, step=None)

    def _translate_expr_aggr_list(self, ctx: AstToIrContext, expr) -> ir.ExprList:
        """Translate a PSS aggregate list literal {1, 2, 3} to ExprList"""
        elts = []
        for i in range(expr.numElems() if hasattr(expr, 'numElems') else 0):
            elem = expr.getElem(i)
            ir_elem = self._translate_expression(ctx, elem)
            if ir_elem is not None:
                elts.append(ir_elem)
        return ir.ExprList(elts=elts)

    def _translate_expr_aggr_map(self, ctx: AstToIrContext, expr) -> ir.ExprDict:
        """Translate a PSS aggregate map literal {k1:v1, k2:v2} to ExprDict"""
        keys = []
        values = []
        for i in range(expr.numElems() if hasattr(expr, 'numElems') else 0):
            elem = expr.getElem(i)
            key_ir = self._translate_expression(ctx, elem.getLhs())
            val_ir = self._translate_expression(ctx, elem.getRhs())
            if key_ir is not None and val_ir is not None:
                keys.append(key_ir)
                values.append(val_ir)
        return ir.ExprDict(keys=keys, values=values)

    def _translate_expr_aggr_struct(self, ctx: AstToIrContext, expr) -> ir.ExprStructLiteral:
        """Translate a PSS struct aggregate literal {.a=1, .b=2} to ExprStructLiteral"""
        fields = []
        for i in range(expr.numElems() if hasattr(expr, 'numElems') else 0):
            elem = expr.getElem(i)
            name_node = elem.getName()
            field_name = name_node.getId() if isinstance(name_node, pss_ast.ExprId) else str(name_node)
            val_ir = self._translate_expression(ctx, elem.getValue())
            if val_ir is not None:
                fields.append(ir.ExprStructField(name=field_name, value=val_ir))
        return ir.ExprStructLiteral(fields=fields)

    # Ordinals below are the declaration order of pssparser's ast::ExprBinOp
    # and ast::ExprUnaryOp. Both maps must stay total: an unmapped operator
    # that falls back to a default is a silently-wrong expression, which is
    # exactly how `**` used to lower to `+`.
    _BINOP_MAP = {
        0: ir.BinOp.Or,       # ||
        1: ir.BinOp.And,      # &&
        2: ir.BinOp.BitOr,    # |
        3: ir.BinOp.BitXor,   # ^
        4: ir.BinOp.BitAnd,   # &
        5: ir.BinOp.Lt,       # <
        6: ir.BinOp.LtE,      # <=
        7: ir.BinOp.Gt,       # >
        8: ir.BinOp.GtE,      # >=
        9: ir.BinOp.Exp,      # **
        10: ir.BinOp.Mult,    # *
        11: ir.BinOp.Div,     # /
        12: ir.BinOp.Mod,     # %
        13: ir.BinOp.Add,     # +
        14: ir.BinOp.Sub,     # -
        15: ir.BinOp.LShift,  # <<
        16: ir.BinOp.RShift,  # >>
        17: ir.BinOp.Eq,      # ==
        18: ir.BinOp.NotEq,   # !=
    }

    _UNARYOP_MAP = {
        0: ir.UnaryOp.UAdd,     # +
        1: ir.UnaryOp.USub,     # -
        2: ir.UnaryOp.Not,      # !
        3: ir.UnaryOp.Invert,   # ~
    }

    # &, |, ^ as unary operators are PSS's bit-reduction operators. The IR has
    # no node for them, so they are refused rather than approximated.
    _UNARYOP_REDUCTION = {4: "&", 5: "|", 6: "^"}

    def _map_binop(self, ctx: AstToIrContext, op: int) -> ir.BinOp:
        """Map PSS binary operator (integer) to IR operator"""
        if op not in self._BINOP_MAP:
            ctx.add_error(
                f"unsupported PSS binary operator (ExprBinOp ordinal {op})")
            return ir.BinOp.Add
        return self._BINOP_MAP[op]

    def _map_unaryop(self, ctx: AstToIrContext, op: int) -> ir.UnaryOp:
        """Map PSS unary operator (integer) to IR operator"""
        if op in self._UNARYOP_REDUCTION:
            ctx.add_error(
                f"the bit-reduction operator '{self._UNARYOP_REDUCTION[op]}' "
                f"is not supported by this compiler")
            return ir.UnaryOp.Not
        if op not in self._UNARYOP_MAP:
            ctx.add_error(
                f"unsupported PSS unary operator (ExprUnaryOp ordinal {op})")
            return ir.UnaryOp.Not
        return self._UNARYOP_MAP[op]

    def _translate_data_type(self, ctx: AstToIrContext, dtype_node: Any) -> Optional[ir.DataType]:
        """Translate a data type node to IR

        Args:
            ctx: Translation context
            dtype_node: PSS data type AST node

        Returns:
            IR DataType or None
        """
        if isinstance(dtype_node, pss_ast.DataTypeInt):
            # Get bit width and signedness
            width = dtype_node.getWidth()
            if width and hasattr(width, 'getValue'):
                bits = width.getValue()
            else:
                bits = 32  # Default

            is_signed = dtype_node.getIs_signed()
            return ir.DataTypeInt(bits=bits, signed=is_signed)

        elif isinstance(dtype_node, pss_ast.DataTypeUserDefined):
            # User-defined type - check if it's a template specialization or simple reference
            type_id = dtype_node.getType_id()

            # Check if this is a TypeIdentifier (template specialization)
            if isinstance(type_id, pss_ast.TypeIdentifier):
                return self._translate_type_identifier(ctx, type_id)
            elif isinstance(type_id, pss_ast.ExprId):
                type_name = type_id.getId()
            else:
                type_name = str(type_id)

            # Check if type exists in registry
            existing_type = ctx.get_type(type_name)
            if existing_type:
                return existing_type
            else:
                # Create reference for forward declaration
                return ir.DataTypeRef(ref_name=type_name)

        elif isinstance(dtype_node, pss_ast.DataTypeString):
            return ir.DataTypeString()

        elif isinstance(dtype_node, pss_ast.DataTypeBool):
            return ctx.get_type("bool")

        elif isinstance(dtype_node, pss_ast.DataTypeChandle):
            return ir.DataTypeChandle()

        else:
            if self.debug:
                self.logger.debug(f"Unsupported data type: {type(dtype_node).__name__}")
            return None

    def _linked_type_name(self, ctx: AstToIrContext, type_id) -> Optional[str]:
        """The qualified name of the type ``type_id`` was LINKED to, or None.

        Follows the linker's `SymbolRefPath` from the symbol root: a child step
        descends, and a super step (`ElemKind_Super`, recorded when the name was
        found in a base type) moves to the base type's scope by following that
        type's own `super_t` link -- as `TaskResolveSymbolPathRef` does. The
        name is the chain of scopes from the root, the key types are filed
        under. None if the path holds any other kind of step.
        """
        get_target = getattr(type_id, "getTarget", None)
        ref = get_target() if get_target is not None else None
        scope = self._symbol_scope_at(ctx, ref, depth=0)
        if scope is None:
            return None
        names: List[str] = []
        s = scope
        while s is not None and s is not ctx.symbol_root \
                and s.getUpper() is not None:
            names.append(s.getName())
            s = s.getUpper()
        return "::".join(reversed(names)) or None

    def _symbol_scope_at(self, ctx: AstToIrContext, ref, depth: int):
        """The symbol scope a `SymbolRefPath` names, or None."""
        root = getattr(ctx, "symbol_root", None)
        if ref is None or root is None or depth > 64:
            return None
        K = pss_ast.SymbolRefPathElemKind
        scope = root
        for pe in ref.getPathList():
            if pe.kind == K.ElemKind_ChildIdx:
                if not hasattr(scope, "numChildren") \
                        or not 0 <= pe.idx < scope.numChildren():
                    return None
                scope = scope.getChild(pe.idx)
            elif pe.kind == K.ElemKind_Super:
                ts = scope.getTarget() if hasattr(scope, "getTarget") else None
                sup = ts.getSuper_t() if hasattr(ts, "getSuper_t") else None
                sref = sup.getTarget() if hasattr(sup, "getTarget") else None
                scope = self._symbol_scope_at(ctx, sref, depth + 1)
                if scope is None:
                    return None
            else:
                return None
        return scope

    def _type_identifier_name(self, node) -> Optional[str]:
        """Extract the (possibly package-qualified) type name from a TypeIdentifier or ExprId.

        For multi-element TypeIdentifiers like ``sys_pkg::base_a``, returns the
        full ``"::"``-joined name.  Single-element identifiers return the name directly.

        Args:
            node: pss_ast.TypeIdentifier or pss_ast.ExprId

        Returns:
            Type name string, or None if extraction fails
        """
        if isinstance(node, pss_ast.ExprId):
            return node.getId()
        if isinstance(node, pss_ast.TypeIdentifier) and node.numElems() > 0:
            parts = []
            for i in range(node.numElems()):
                elem_id = node.getElem(i).getId()
                nm = elem_id.getId() if isinstance(elem_id, pss_ast.ExprId) else str(elem_id)
                parts.append(nm)
            return "::".join(parts)
        return None

    def _translate_type_identifier(self, ctx: AstToIrContext, type_id: pss_ast.TypeIdentifier) -> Optional[ir.DataType]:
        """Translate a TypeIdentifier (potentially with template parameters)

        Args:
            ctx: Translation context
            type_id: TypeIdentifier AST node

        Returns:
            IR DataType (may be DataTypeRegister for reg_c specializations)
        """
        # TypeIdentifier has elems list - get the first element
        if type_id.numElems() == 0:
            return None

        elem = type_id.getElem(0)
        elem_id = elem.getId()

        # Get the base type name
        if isinstance(elem_id, pss_ast.ExprId):
            type_name = elem_id.getId()
        else:
            type_name = str(elem_id)

        # Check if this is a reg_c specialization
        if type_name == "reg_c":
            return self._translate_reg_c(ctx, elem)

        # `sync_pkg::channel_c<Te, DEPTH>` -- a core-library parameterized
        # component, like reg_c, and translated the same way: to a datatype that
        # CARRIES its template arguments, not to an ordinary DataTypeComponent.
        #
        # The distinction is not cosmetic. A component's template arguments do
        # not survive into the IR, so a channel that reached a backend as a
        # component arrived with no element type and no depth -- and a backend
        # cannot invent either. `channel_c` is also not a type a backend can
        # emit from the model: its body is the RUNTIME's (a mailbox in SV, a
        # FIFO in C), so lowering it as a user component produced a class with
        # the right name and no methods in it.
        #
        # Accepted qualified as well as bare: `import sync_pkg::*` makes the
        # bare form usual, but `sync_pkg::channel_c<...>` is the same type and
        # must not fall through to the generic path.
        last = type_id.getElem(type_id.numElems() - 1)
        last_id = last.getId()
        last_name = last_id.getId() if isinstance(last_id, pss_ast.ExprId) else str(last_id)
        if last_name == "channel_c":
            return self._translate_channel_c(ctx, last)

        # Handle built-in collection types
        if type_name in ("list", "array", "map", "set"):
            return self._translate_collection_type(ctx, type_name, elem)

        # Build full qualified name for package-scoped types (e.g. sys_pkg::base_a)
        if type_id.numElems() > 1:
            parts = []
            for i in range(type_id.numElems()):
                e_id = type_id.getElem(i).getId()
                nm = e_id.getId() if isinstance(e_id, pss_ast.ExprId) else str(e_id)
                parts.append(nm)
            qualified_name = "::".join(parts)
        else:
            qualified_name = type_name

        # Check type_map first (handles enums, typedefs, structs, components)
        existing = ctx.get_type(qualified_name)
        if existing is not None:
            return existing
        # Fallback: try short name when only a suffix is available
        if "::" in qualified_name:
            short = qualified_name.split("::")[-1]
            existing = ctx.get_type(short)
            if existing is not None:
                return existing

        # Fall back to forward reference
        return ir.DataTypeRef(ref_name=qualified_name)

    def _translate_channel_c(
        self,
        ctx: AstToIrContext,
        elem: pss_ast.TypeIdentifierElem,
    ) -> ir.DataTypeChannel:
        """Translate ``sync_pkg::channel_c<Te, DEPTH>`` (PSS 3.1 §21.9.1).

        ``DEPTH`` defaults to 1, per the LRM, and that default is load-bearing
        rather than incidental: a depth-1 channel is a coalescing binary
        semaphore, which is what an interrupt-wake channel wants. Getting the
        default wrong would not fail anywhere -- it would give the model a
        deeper buffer than it asked for and quietly stop coalescing.

        Args:
            ctx: Translation context
            elem: TypeIdentifierElem carrying the template parameters

        Returns:
            DataTypeChannel with the element type and depth resolved
        """
        element_type = None
        depth = 1

        params = elem.getParams()
        if params is not None:
            if params.numValues() > 0:
                inner = params.getValue(0).getValue()
                if inner is not None:
                    element_type = self._translate_data_type(ctx, inner)

            # DEPTH may be a literal (`channel_c<bit,1>`) or a named constant
            # (`channel_c<bit,WAKE_DEPTH>`); fold either. A depth that does not
            # fold is left at the LRM default rather than becoming -1, because
            # -1 reaches a backend as a buffer size and there is no size that
            # means "unknown".
            if params.numValues() > 1:
                inner = params.getValue(1).getValue()
                folded = None
                if inner is not None and hasattr(inner, 'getValue'):
                    v = inner.getValue()
                    if isinstance(v, int) and not isinstance(v, bool):
                        folded = v
                if folded is None:
                    folded = self._fold_const_expr(ctx, inner)
                if folded is not None:
                    depth = folded

        # "DEPTH, if specified, shall be positive" (§21.9.1). Rejected here
        # rather than passed on: SV's `mailbox #(T) m = new(0)` is UNBOUNDED, so
        # a zero would not fail downstream, it would silently produce a channel
        # that never coalesces and never blocks a writer.
        if depth < 1:
            ctx.add_error(f"channel_c DEPTH shall be positive (got {depth})")
            depth = 1

        # A readable specialization name. Primitive element types carry no
        # `name` in the IR, so `bit` would otherwise print as the unsubstituted
        # parameter `Te` -- a name that reads like the translation failed.
        label = getattr(element_type, 'name', None)
        if label is None and isinstance(element_type, ir.DataTypeInt):
            label = ("int" if element_type.signed and element_type.bits == 32
                     else ("bit" if element_type.bits == 1
                           else f"bit[{element_type.bits}]"))
        return ir.DataTypeChannel(
            name=f"channel_c<{label or 'Te'},{depth}>",
            element_type=element_type,
            depth=depth,
        )

    def _translate_collection_type(
        self,
        ctx: AstToIrContext,
        coll_name: str,
        elem: pss_ast.TypeIdentifierElem,
    ) -> Optional[ir.DataType]:
        """Translate a built-in PSS collection type to the corresponding IR type.

        Supports ``list<T>``, ``array<T, N>``, ``map<K, V>``, and ``set<T>``.

        Args:
            ctx: Translation context
            coll_name: One of "list", "array", "map", "set"
            elem: TypeIdentifierElem carrying the template parameters

        Returns:
            DataTypeList | DataTypeArray | DataTypeMap | DataTypeSet
        """
        params = elem.getParams()
        if params is None:
            return None

        def get_type_param(index: int) -> Optional[ir.DataType]:
            """Extract a data-type template parameter at ``index``."""
            if index >= params.numValues():
                return None
            pv = params.getValue(index)
            inner = pv.getValue()
            if inner is None:
                return None
            return self._translate_data_type(ctx, inner)

        def get_int_param(index: int) -> int:
            """Extract an integer-valued template parameter at ``index``.

            The value may be a literal (``ch[4]``) or a reference to a
            package-scope constant (``ch[WB_DMA_MAX_CH]``) -- the parser lowers
            ``T x[N]`` to ``array<T, N>`` either way. Only the literal case used
            to fold, so a model that names its channel count (as a real one
            does) got ``size=-1``.
            """
            if index >= params.numValues():
                return -1
            pv = params.getValue(index)
            inner = pv.getValue()
            if inner is not None and hasattr(inner, 'getValue'):
                v = inner.getValue()
                if isinstance(v, int) and not isinstance(v, bool):
                    return v
            folded = self._fold_const_expr(ctx, inner)
            return folded if folded is not None else -1

        if coll_name == "list":
            return ir.DataTypeList(element_type=get_type_param(0))

        elif coll_name == "array":
            return ir.DataTypeArray(
                element_type=get_type_param(0),
                size=get_int_param(1),
            )

        elif coll_name == "map":
            return ir.DataTypeMap(
                key_type=get_type_param(0),
                value_type=get_type_param(1),
            )

        elif coll_name == "set":
            return ir.DataTypeSet(element_type=get_type_param(0))

        return None

    def _translate_reg_c(self, ctx: AstToIrContext, elem: pss_ast.TypeIdentifierElem) -> ir.DataTypeRegister:
        """Translate a reg_c<R, ACC, SZ> template specialization to DataTypeRegister

        Args:
            ctx: Translation context
            elem: TypeIdentifierElem with template parameters

        Returns:
            DataTypeRegister IR node
        """
        # Extract template parameters
        params = elem.getParams()

        # Default values per PSS spec
        register_value_type = None
        access_mode = "READWRITE"
        size_bits = None
        template_args = []

        if params and params.numValues() > 0:
            # First parameter: R (type)
            param0 = params.getValue(0)
            if isinstance(param0, pss_ast.TemplateParamTypeValue):
                # Get the data type
                dtype = param0.getValue()
                register_value_type = self._translate_data_type(ctx, dtype)

                # Store template arg for completeness
                if register_value_type:
                    template_args.append(ir.TemplateArgType(
                        param_name="R",
                        type_value=register_value_type
                    ))

            # Second parameter: ACC (enum - access mode)
            # Note: This can be either TemplateParamTypeValue or TemplateParamExprValue
            if params.numValues() > 1:
                param1 = params.getValue(1)

                # Try both TemplateParamTypeValue and TemplateParamExprValue
                expr = None
                if isinstance(param1, pss_ast.TemplateParamTypeValue):
                    # READONLY/READWRITE/WRITEONLY might come as a type
                    dtype = param1.getValue()
                    # dtype should be DataTypeUserDefined with identifier READONLY etc
                    if isinstance(dtype, pss_ast.DataTypeUserDefined):
                        type_id_acc = dtype.getType_id()
                        if isinstance(type_id_acc, pss_ast.ExprId):
                            access_mode = type_id_acc.getId()
                        elif isinstance(type_id_acc, pss_ast.TypeIdentifier):
                            # Handle TypeIdentifier case
                            if type_id_acc.numElems() > 0:
                                elem_acc = type_id_acc.getElem(0)
                                elem_id_acc = elem_acc.getId()
                                if isinstance(elem_id_acc, pss_ast.ExprId):
                                    access_mode = elem_id_acc.getId()
                elif isinstance(param1, pss_ast.TemplateParamExprValue):
                    # Get the expression (should be an identifier like READONLY)
                    expr = param1.getValue()

                    if isinstance(expr, pss_ast.ExprId):
                        access_mode = expr.getId()
                    elif isinstance(expr, pss_ast.ExprRefPathStatic):
                        # Handle hierarchical references like addr_reg_pkg::READONLY
                        if expr.numBase() > 0:
                            last_elem = expr.getBase(expr.numBase() - 1)
                            if isinstance(last_elem, pss_ast.TypeIdentifierElem):
                                elem_id = last_elem.getId()
                                if isinstance(elem_id, pss_ast.ExprId):
                                    access_mode = elem_id.getId()
                    elif hasattr(expr, 'getLeaf'):
                        # Try ExprRefPathContext or similar
                        leaf = expr.getLeaf()
                        if leaf and hasattr(leaf, 'getId'):
                            access_mode = leaf.getId()
                    elif hasattr(expr, 'getId'):
                        # Fallback: try direct getId
                        access_mode = expr.getId()

                template_args.append(ir.TemplateArgEnum(
                    param_name="ACC",
                    enum_value=access_mode
                ))

            # Third parameter: SZ2 (int - size in bits)
            if params.numValues() > 2:
                param2 = params.getValue(2)
                if isinstance(param2, pss_ast.TemplateParamExprValue):
                    expr = param2.getValue()
                    if isinstance(expr, pss_ast.ExprNumber):
                        size_bits = expr.getValue()

                        template_args.append(ir.TemplateArgValue(
                            param_name="SZ2",
                            value_expr=ir.ExprConstant(value=size_bits)
                        ))

        # Calculate size_bits if not explicitly provided
        if size_bits is None and register_value_type:
            # Default: 8 * sizeof(R), rounded to byte boundary
            if isinstance(register_value_type, ir.DataTypeInt):
                size_bits = register_value_type.bits
            else:
                size_bits = 32  # Conservative default

        if size_bits is None:
            size_bits = 32  # Absolute fallback

        # Ensure we have register_value_type
        if register_value_type is None:
            # Fallback for missing type parameter
            register_value_type = ir.DataTypeInt(bits=size_bits, signed=False)

        # Create DataTypeRegister
        reg = ir.DataTypeRegister(
            name=f"reg_c<{register_value_type.name if hasattr(register_value_type, 'name') else 'T'}>",
            super=None,  # reg_c doesn't have explicit super type
            register_value_type=register_value_type,
            access_mode=access_mode,
            size_bits=size_bits,
            template_args=template_args
        )

        # Add built-in register functions
        self._add_register_functions(ctx, reg)

        # Extract fields if register uses a struct
        self._extract_register_fields(ctx, reg)

        return reg

    def _add_register_functions(self, ctx: AstToIrContext, reg: ir.DataTypeRegister):
        """Add built-in functions to a register (read, write, read_val, write_val)

        Args:
            ctx: Translation context
            reg: Register to add functions to
        """
        # read() - returns the register value type
        read_func = ir.Function(
            name="read",
            args=ir.Arguments(args=[]),  # No arguments
            body=[],  # import target has no body
            returns=reg.register_value_type,
            is_async=False,
            is_import=True,
            is_target=True
        )
        reg.functions.append(read_func)

        # write(val) - takes register value type, returns void
        write_arg = ir.Arg(
            arg="r",  # Parameter name per PSS spec
            annotation=None  # Type info in DataType, not Expr
        )
        write_func = ir.Function(
            name="write",
            args=ir.Arguments(args=[write_arg]),
            body=[],
            returns=None,  # void
            is_async=False,
            is_import=True,
            is_target=True
        )
        reg.functions.append(write_func)

        # read_val() - returns raw integer
        read_val_func = ir.Function(
            name="read_val",
            args=ir.Arguments(args=[]),
            body=[],
            returns=ir.DataTypeInt(bits=reg.size_bits, signed=False),
            is_async=False,
            is_import=True,
            is_target=True
        )
        reg.functions.append(read_val_func)

        # write_val(val) - takes raw integer
        write_val_arg = ir.Arg(
            arg="r",  # Parameter name per PSS spec
            annotation=None
        )
        write_val_func = ir.Function(
            name="write_val",
            args=ir.Arguments(args=[write_val_arg]),
            body=[],
            returns=None,
            is_async=False,
            is_import=True,
            is_target=True
        )
        reg.functions.append(write_val_func)

        self._add_register_rmw_functions(ctx, reg)

    def _add_register_rmw_functions(self, ctx: AstToIrContext, reg: ir.DataTypeRegister):
        """Declare the PSS 3.1 §21.14.1 masked / field-wise writes.

        All four mean one thing --
        ``REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)`` -- so
        ``reg_rmw`` reduces the other three to ``write_val_masked`` before any
        backend sees them. They are declared here anyway, because a declaration
        is what makes the method resolvable at all; what a backend must
        implement is only the one they reduce to.

        The three that take a field name or a struct-shaped mask are declared
        only when the register's value type is a struct: on a
        ``reg_c<bit[32]>`` there is nothing to name, and an undeclared method is
        a better answer than one that always fails.
        """
        def fn(name, arg_names):
            return ir.Function(
                name=name,
                args=ir.Arguments(args=[ir.Arg(arg=a, annotation=None)
                                        for a in arg_names]),
                body=[],
                returns=None,
                is_async=False,
                is_import=True,
                is_target=True
            )

        # write_val_masked(mask, val) -- the primitive, always available.
        reg.functions.append(fn("write_val_masked", ["mask", "val"]))

        if not self._reg_has_struct_value(ctx, reg):
            return

        reg.functions.append(fn("write_masked", ["mask", "val"]))
        reg.functions.append(fn("write_field", ["name", "val"]))
        reg.functions.append(fn("write_fields", ["names", "vals"]))

    def _reg_has_struct_value(self, ctx: AstToIrContext, reg: ir.DataTypeRegister) -> bool:
        vt = reg.register_value_type
        if isinstance(vt, ir.DataTypeRef):
            vt = ctx.get_type(vt.ref_name) or vt
        return isinstance(vt, ir.DataTypeStruct)

    def _extract_register_fields(self, ctx: AstToIrContext, reg: ir.DataTypeRegister):
        """Extract fields from register value type if it's a struct

        Args:
            ctx: Translation context
            reg: Register to extract fields into
        """
        # If register_value_type is a struct, copy its fields to the register
        value_type = reg.register_value_type

        # Handle DataTypeRef - resolve to actual type
        if isinstance(value_type, ir.DataTypeRef):
            resolved = ctx.get_type(value_type.ref_name)
            if resolved:
                value_type = resolved

        # If it's a struct, copy its fields
        if isinstance(value_type, ir.DataTypeStruct):
            for field in value_type.fields:
                reg.fields.append(field)

    def _add_register_group_functions(self, ctx: AstToIrContext, reg_group: ir.DataTypeRegisterGroup):
        """Add built-in functions to a register group

        Args:
            ctx: Translation context
            reg_group: Register group to add functions to
        """
        # get_offset_of_instance(string name) -> bit[64]
        name_arg = ir.Arg(
            arg="name",
            annotation=None
        )
        offset_func = ir.Function(
            name="get_offset_of_instance",
            args=ir.Arguments(args=[name_arg]),
            body=[],
            returns=ir.DataTypeInt(bits=64, signed=False),
            is_async=False
        )
        reg_group.functions.append(offset_func)

        # get_offset_of_instance_array(string name, bit[32] index) -> bit[64]
        index_arg = ir.Arg(
            arg="index",
            annotation=None
        )
        offset_array_func = ir.Function(
            name="get_offset_of_instance_array",
            args=ir.Arguments(args=[name_arg, index_arg]),
            body=[],
            returns=ir.DataTypeInt(bits=64, signed=False),
            is_async=False
        )
        reg_group.functions.append(offset_array_func)

    def _compute_register_offsets(self, ctx: AstToIrContext, reg_group: ir.DataTypeRegisterGroup):
        """Compute sequential offsets for registers in a register group

        Args:
            ctx: Translation context
            reg_group: Register group to compute offsets for
        """
        current_offset = 0

        for field in reg_group.fields:
            # Check if this field is a register
            field_type = field.datatype

            # Resolve DataTypeRef if needed
            if isinstance(field_type, ir.DataTypeRef):
                resolved = ctx.get_type(field_type.ref_name)
                if resolved:
                    field_type = resolved

            # Only process register fields
            if isinstance(field_type, ir.DataTypeRegister):
                # Store offset for this register
                reg_group.offset_map[field.name] = current_offset

                # Compute size in bytes (round up to nearest byte)
                size_bytes = (field_type.size_bits + 7) // 8

                # Apply 4-byte alignment (minimum alignment for registers)
                aligned_size = ((size_bytes + 3) // 4) * 4

                # Advance offset
                current_offset += aligned_size


    # ---------------------------------------------------------------------------
    # Covergroup translation (from the real Covergroup AST node)
    # ---------------------------------------------------------------------------

    @staticmethod
    def _id_name(id_obj) -> str:
        """Return the string name of an ExprId (or empty)."""
        if id_obj is None:
            return ''
        getter = getattr(id_obj, 'getId', None)
        return getter() if getter else str(id_obj)

    def _translate_covergroup(self, ctx: AstToIrContext, cg_node) -> Optional[object]:
        """Translate a Covergroup AST node into an IR PssCoverGroup.

        Uses the singular ``num*()``/``get*(i)`` accessors (the plural list
        accessors wrap each element via ``accept`` and yield ``None``).
        """
        from zuspec.ir.core.coverage import PssCoverGroup, PssCoverPoint, PssCoverCross

        instance_name = self._id_name(cg_node.getName()) or 'cg'

        coverpoints = []
        for i in range(cg_node.numCoverpoints()):
            cp = cg_node.getCoverpoint(i)
            if cp is None:
                continue
            cp_name = self._id_name(cp.getName())
            target = cp.getTarget()
            target_expr = (self._translate_expression(ctx, target)
                           if target is not None else None)
            if target_expr is None:
                target_expr = ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=cp_name)
            coverpoints.append(PssCoverPoint(name=cp_name, target_expr=target_expr))

        crosses = []
        for i in range(cg_node.numCrosses()):
            cx = cg_node.getCrosse(i)
            if cx is None:
                continue
            cx_name = self._id_name(cx.getName()) or 'cross'
            names = []
            for j in range(cx.numCoverpoint_names()):
                e = cx.getCoverpoint_name(j)
                if e is not None:
                    names.append(self._id_name(e))
            crosses.append(PssCoverCross(name=cx_name, coverpoint_names=names))

        return PssCoverGroup(
            instance_name=instance_name,
            coverpoints=coverpoints,
            crosses=crosses,
        )


def _activity_body_children(body):
    """Return an iterable of children for an activity body scope (or empty)."""
    if body is None:
        return []
    if hasattr(body, 'children'):
        return body.children()
    return []
