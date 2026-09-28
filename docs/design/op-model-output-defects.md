# Op-Model Output Defects

Status: 2026-09-24. Also published as <https://claude.ai/artifact/GB4PbwVCVtFBXskyUtDGwX> (private).

This list covers problems in the code pssc generates for `op-model-sv`, `op-model-c`, `op-model-cpp` and `op-model-py`. These are the kind that make people distrust generated code. They were found while adding component inheritance and `super`.

Each open item gives:
- what a user sees,
- why it matters,
- what fixes it.

The ranking is a proposal.

## How the items are graded

| Class | Meaning |
|---|---|
| **Silent-wrong** | Compiles, runs, and does something else. The worst class: nothing tells the user. |
| **Invalid, unreported** | pssc succeeds, and the user's compiler or simulator then rejects the output. pssc should have said so. |
| **Wrong shape** | Behaviour is correct, but it isn't code a person in that language would write or could build on. |
| **Refused** | pssc gives a clear error. It's a gap, but an honest one, so it's least urgent unless the message is bad. |

## Open items, proposed order

| ID | Pri | Issue | Targets | Class |
|---|---|---|---|---|
| [SV-1](#sv-1-inheritance-is-flattened) | ~~P0~~ | ~~Inheritance is flattened~~ Fixed: native classes, `extends`, `super.f()` (plan P5) | SV | Wrong shape (and invalid) |
| [SV-2](#sv-2-value-returning-calls-inside-expressions-are-not-hoisted) | P0 | Value-returning calls inside expressions are not hoisted. Fixed for SV (hoisted to temporaries); C coroutines open | SV, C (coroutines) | Invalid, unreported |
| [SV-3](#sv-3-sub-components-are-only-constructed-by-a-pss-constructor) | ~~P0~~ | ~~Sub-components are only constructed by a PSS constructor~~ Fixed (plan P2) | SV | Silent-wrong |
| [SV-4](#sv-4-repeat-is-not-lowered) | ~~P1~~ | ~~`repeat` is not lowered~~ Fixed | SV | Refused |
| [SV-5](#sv-5-a-discarded-register-read-does-not-compile) | ~~P1~~ | ~~A discarded register read does not compile~~ Fixed | SV | Invalid, unreported |
| [C-1](#c-1-inheritance-is-flattened-private-copies-are-public-api) | P1 | Inheritance is flattened; private copies are public API | C | Wrong shape |
| [X-1](#x-1-bit-slices-are-rejected-everywhere-with-an-internal-message) | P1 | Bit slices are rejected everywhere, with an internal message | all | Refused |
| [X-2](#x-2-d-prints-an-unsigned-value-as-signed-at-its-own-width) | P1 | `%d` prints an unsigned value as signed at its own width | Python (and bc) | Silent-wrong |
| [FE-1](#fe-1-hex-literals-are-typed-as-signed-int-re-verify) | P1 | Hex literals are typed as signed `int` (re-verify) | front end | Silent-wrong |
| [C-2](#c-2-op-model-c-cannot-call-a-sub-components-operation) | P1 | op-model-c cannot call a sub-component's operation | C | Refused |
| [CPP-2](#cpp-2-a-function-shadowed-with-another-signature-fails--werror) | P1 | A function shadowed with another signature fails `-Werror` | C++ | Invalid, unreported |
| [CPP-1](#cpp-1-op-model-cpp-cannot-call-an-operation-two-sub-components-down) | P2 | op-model-cpp cannot call an operation two sub-components down | C++ | Refused |
| [X-3](#x-3-emitter-refusals-carry-no-source-location) | P2 | Refusals raised during code generation carry no source location | all | Refused |
| [PY-1](#py-1-template-struct-fields-are-not-specialized-re-verify) | P2 | Template struct fields are not specialized (re-verify) | Python | Silent-wrong |
| [FE-2](#fe-2-a-missing--between-exec-statements-is-accepted-re-verify) | P2 | A missing `;` between exec statements is accepted (re-verify) | front end | Silent-wrong |
| [FE-3](#fe-3-a-solve-import-called-from-a-target-function-is-not-refused) | P2 | A solve import called from a target function is not refused | front end | Silent-wrong |
| [INH-1](#inh-1-remaining-inheritance-refusals) | P3 | Remaining inheritance refusals | all | Refused |
| [LRM-1](#lrm-1-super-only-at-the-top-level-of-an-exec-block) | P3 | `super;` only at the top level of an exec block | front end | Refused |

---

### SV-1: Inheritance is flattened

**P0 · SV · Wrong shape, and invalid output**

- **What users see:**
  - A derived component's class does not extend its base's; every inherited member is copied into it.
  - The base's version of an overridden function appears as a private copy in the public interface class.
  - The flattened `super` call is a task called inside an expression (see SV-2), which is illegal SV.

  ```systemverilog
  interface class pss_top_if;
    pure virtual task _pss_super_base_c_f(output int status, input int x);
    ...
  class pss_top implements pss_top_if;
    protected int m__pss_super_base_c_a;
    ...
    status = (_pss_super_base_c_f(x) + m_a) + m__pss_super_base_c_a;
  ```
- **Why it matters:** flattening is categorically wrong for SystemVerilog, even apart from the task restriction.
  - A `der_c` handle is not a `base_c`, so it can't be assigned or passed where a base is expected.
  - Users can't extend or override the generated classes in SV.
  - Code is duplicated in every derived class.
  - pssc's private implementation names appear in the API.
- **Fix:** the proposed shape and the decisions it needs are in [SV Op-Model: Native Inheritance](sv-op-model-inheritance.md).
  - Generate native `class der_c extends base_c`, `super.f(...)` and `super.m_a`, as op-model-py and op-model-cpp now do.
  - SV currently folds the PSS constructor into `new()`. It first needs construction split: `new(imp)` calls `super.new(imp)`, and the op-model constructor becomes a separate method.
  - Until native classes land, SV should **refuse** inheritance rather than flatten it.

### SV-2: Value-returning calls inside expressions are not hoisted

**P0 · SV, C (coroutines) · Invalid, unreported**

- **What users see:**
  - A PSS operation that returns a value becomes a task with an `output` argument.
  - Only a whole assignment (`q = g();` becomes `g(q);`) is lowered correctly.
  - Anywhere else, the task is called as if it were a function:

  ```systemverilog
  int r = m_s.f(1);          // task called as a function
  int z = g() + 1;
  $display("%d", m_b.f(1));
  ```
- **Why it matters:**
  - pssc reports success, and then the simulator fails with `Cannot call a task/void-function as a function`.
  - Almost any real body calls something inside an expression.
  - C coroutines have the same shape.
- **Fix:**
  - Hoist every call that becomes a task into a temporary, as op-model-py's `_awaited` already does.
  - A loop whose condition makes such a call becomes bottom-tested, so the call runs on every iteration.
  - Build this once and share it between SV and C coroutines.
- **Fixed for SV** (`sv/lower_progseq.py`, "hoisting blocking calls out of expressions"): a blocking value call inside an expression gets a temporary, is emitted in front of the statement, and the temporary takes its place.
  - Calls are hoisted in evaluation order, arguments first.
  - Temporaries are declared at the top of the enclosing block.
  - A conditionally evaluated operand keeps its condition: the right operand of `&&`/`||` and each arm of `?:` hoist under an `if`.
  - A loop condition is hoisted into the loop, so it runs every iteration. A `repeat … while` whose body has a `continue` tests at the top of each iteration after the first, so `continue` still reaches the test.
  - Held by `test_sv_op_model_sim.py::test_blocking_calls_are_hoisted_without_changing_what_runs`, which runs under Verilator. C coroutines are still open.

### SV-3: Sub-components are only constructed by a PSS constructor

**P0 · SV · Silent-wrong**

- **What users see:** a sub-component is created only if the model's constructor calls something like `ch[i].initialize(...)`. Otherwise its handle stays null:

  ```systemverilog
  class pss_top ...;
    protected sub_c m_s;
    function new(IMP_T imp);
      m_imp = imp;           // m_s never constructed
    endfunction
  ```
- **Why it matters:**
  - The design elaborates, and the first call through the sub-component is a null dereference at run time.
  - The instance exists in PSS whether or not anything constructs it, and Python and C++ both build it.
- **Fix:** construct every sub-component whose type has no constructor, as op-model-py (`_sub_storage`) and op-model-cpp do.
- **Fixed** by [plan](sv-op-model-inheritance-plan.md) P2: `new(imp)` constructs every sub-component, and the model's constructor is a method called on the built instance (design D2, D3).

### SV-4: `repeat` is not lowered

**P1 · SV · Refused**

- **What users see:** `repeat (n) { ... }` fails with `unsupported stmt StmtFor: _BodyEmitter defines no stmt_for()`.
- **Why it matters:**
  - Polling a status register N times is the most common loop in a driver.
  - The message names pssc's internals, not the construct (see X-3).
- **Fix:** add `stmt_for` to the SV emitter (`repeat (n) begin ... end`, or a counted `for`), as Python already has.
- **Fixed:** `repeat (n)` without an index, and a counted `for` over a local holding the count when there is one.

### SV-5: A discarded register read does not compile

**P1 · SV · Invalid, unreported**

- **What users see:** `regs.STAT.read_val();` as a statement becomes `m_regs.STAT.read_val();`. `read_val` is a task with an `output` argument, so Verilator reports `Missing argument on non-defaulted argument 'v'`. pssc exits 0.
- **Why it matters:** a read for its side effect is how a read-to-clear status register is written.
- **Fix:** give the read a temporary, as `_discarded_get` already does for a channel `get`.
- **Workaround:** assign the value (`s = regs.STAT.read_val();`), which is lowered to the task form.

### C-1: Inheritance is flattened; private copies are public API

**P1 · C · Wrong shape**

- **What users see:** inherited bodies are copied into each derived component, and `super.f` becomes an exported function:

  ```c
  struct pss_top { int _pss_super_base_c_a; ... };
  int pss_top__pss_super_base_c_f(pss_top_t *s, int x);
  ```
- **Why it matters:**
  - A derived component can't be used where its base is expected.
  - Code is duplicated in every derived component.
  - pssc's private names become part of the C API that users link against.
- **Fix:** a per-component function table (vtable).
  - The base struct is embedded first in the derived struct.
  - `super` calls go directly through the base's table.
  - The flattened view (`comp_inherit`) remains for analysis only.

### X-1: Bit slices are rejected everywhere, with an internal message

**P1 · all targets · Refused**

- **What users see:**

  ```
  return v[15:8];
  → unsupported expr ExprSlice: _BodyEmitter defines no expr_slice().
  ```
- **Why it matters:**
  - Register programming is full of slices.
  - The refusal is honest, but the message names pssc's own classes instead of the PSS construct and its line, so users read it as a crash.
- **Fix:**
  - Lower `ExprSlice` as shift-and-mask in all four emitters.
  - Until then, make every "no hook for this node" error name the PSS construct and its location.

### X-2: `%d` prints an unsigned value as signed at its own width

**P1 · Python (and bc) · Silent-wrong**

- **What users see:** `bit[8] x = 200; message(NONE, "%d", x)` prints `-56`.

  ```
  bit[8]  200    → "-56"
  bit[16] 40000  → "-25536"
  ```
- **Why it matters:**
  - LRM 21.1.1 c) says only that the value "is converted to signed type".
  - pssc reads that as reinterpreting the value at its own width, which turns a register value into a negative number in a log.
  - The value-preserving reading prints 200.
- **Fix:** decide which reading is correct. If it's the value-preserving one:
  - change `_pss_fmt` and bc together;
  - add a corpus test that settles it.

### FE-1: Hex literals are typed as signed `int` (re-verify)

**P1 · front end · Silent-wrong**

- **What users see:**
  - The IR's `ExprConstant` keeps neither a literal's radix nor its width, so `0xFFFFFFFF` is typed as a signed `int`.
  - LRM Table 21 types it as `bit[N]`.
- **Why it matters:** the type decides wrapping, comparisons and `%d` output, so the same literal behaves differently depending on how it was written.
- **Fix:** an optional type on ir-core `ExprConstant`, set by ast2ir from the AST literal. This IR change is still waiting on a decision.

### C-2: op-model-c cannot call a sub-component's operation

**P1 · C · Refused**

- **What users see:** a parent calling `d.f(2)` gets `call to 'ExprAttribute' has no lowering in the C target`.
- **Why it matters:** calling down the tree is ordinary structure. C can't do it at all, while Python and C++ can.
- **Fix:** render sub-component calls through the embedded sub-structs, e.g. `<sub>_f(&s->d, ...)`.

### CPP-2: A function shadowed with another signature fails `-Werror`

**P1 · C++ · Invalid, unreported** (introduced with native C++ inheritance, 2026-09-24)

- **What users see:** a derived `scale()` that shadows the base's `scale(int)` (LRM Example 284's shape) is emitted as:

  ```cpp
  struct base_if { virtual int scale(int v) = 0; ... };
  struct der_if : public virtual base_if { virtual int scale() = 0; ... };
  ```

  g++ with `-Wall -Werror` then fails: `'virtual int base_if::scale(int)' was hidden [-Werror=overloaded-virtual=]`.
- **Why it matters:** PSS allows the shape (Table 27: "shadow"). Many projects build with warnings as errors, so pssc's output fails their build.
- **Fix:** emit a `using base::scale;` in the derived class's `protected` section. That keeps the base's `scale(int)` reachable only by the derived class's own code, which is where `super.scale(...)` lives, and it is no longer hidden. The same question arises for SV, where a virtual override must match the base's prototype exactly (see [SV Op-Model: Native Inheritance](sv-op-model-inheritance.md)).

### CPP-1: op-model-cpp cannot call an operation two sub-components down

**P2 · C++ · Refused**

- **What users see:** `m.lf.ping()` is refused; only one level (`this->m_.f()`) is lowered.
- **Why it matters:** deeper hierarchies are normal, and the inheritance tests had to route around this.
- **Fix:** walk the full sub-component chain in `_sub_op_call`.

### X-3: Emitter refusals carry no source location

**P2 · all targets · Refused**

- **What users see:**
  - The call gate names the component and function (`pss_top::run`), though not the line.
  - Refusals raised while generating code carry no line at all. Examples: `set_executor`, `super;` outside an exported action, a bit slice, a non-literal `message` format.
- **Why it matters:**
  - An error without a location sends the user searching through the model.
  - It is the most-cited complaint about generators.
- **Fix:** either of these approaches:
  - carry source locations into the IR (ast2ir doesn't stamp them today) and move these refusals into the call gate;
  - or give all of them one common error type that carries a location.

### PY-1: Template struct fields are not specialized (re-verify)

**P2 · Python · Silent-wrong**

- **What users see:**
  - A field typed by a template parameter (`addr_region_s<TRAIT>.trait`) reaches the IR as `DataTypeRef('TRAIT')`.
  - op-model-py then defaults it to `None`.
- **Why it matters:** anything that reads through the field fails at run time, far from where the struct was declared.
- **Fix:** specialize template struct types in the IR, in the front end.

### FE-2: A missing `;` between exec statements is accepted (re-verify)

**P2 · front end · Silent-wrong**

- **What users see:** pssparser accepts two exec-body statements with no separator (`a = 1  b = 2;`), and no test records it.
- **Why it matters:** a typo that should be a syntax error becomes a different parse.
- **Fix:** fix the grammar in pssparser and add a negative test.

### FE-3: A solve import called from a target function is not refused

**P2 · front end · Silent-wrong**

- **What users see:**
  - LRM 20.2.1.3 makes it illegal.
  - The gate enforces only the reverse direction, because `test_c_imports.py` relies on the illegal form.
- **Why it matters:** a model that is wrong by the standard compiles, and the illegal form spreads through test models.
- **Fix:** decide it, then fix the tests that depend on it.

### INH-1: Remaining inheritance refusals

**P3 · all targets · Refused**

- **What users see:** a clear error, only for components actually in use, in these cases:
  - redeclaring an inherited sub-component or register block;
  - overriding, with a different signature, a function that an inherited function calls;
  - `super.a` in a function that also has a parameter named `a`.
- **Why it matters:** these are rare, but legal PSS.
- **Fix:**
  - Redeclaring an instance needs two names for a register subtree.
  - The other two cases need the front end to tell a parameter from a field.

### LRM-1: `super;` only at the top level of an exec block

**P3 · front end · Refused**

- **What users see:**
  - The grammar (Syntax 92, Annex B.7) allows `super;` only as a direct `exec_stmt`.
  - So `if (c) { super; }` is a syntax error (`PSS028`).
- **Why it matters:** conditionally reusing base behaviour is a natural thing to write.
- **Status:** tracked as a likely LRM defect.
- **Fix:** extend pssparser once the LRM question is settled. pssc already handles `super;` at any depth.

---

## SV inheritance shape

The worked example and the design decisions for SV-1 are in a separate document: [SV Op-Model: Native Inheritance](sv-op-model-inheritance.md).

---

## Fixed while finding these

These are listed because they're the same class of problem and show how it arises. Each one compiled without complaint and produced a wrong program. None is committed yet.

- **A parameter named like a field read the field**, on all four targets. With a field `a = 100`, `f(int a) { return a * 2; }` returned 200: the output read `self.a`, `s->a`, `m_a` or `this->a`.
- **op-model-cpp dropped field initializers** unless the component declared a PSS constructor: `int a = 5;` became `int a{};`, which is 0.
- **op-model-py shared one base address across all of a component's register groups.** With two groups bound to different handles, every access landed at the second one's address.
- **Every op-model target dropped whatever a derived component inherited**: its fields, sub-components, register blocks and functions.
- **The front end dropped `super;`**, and any other statement it couldn't translate (`randomize`, for one).
- **`super.f()` compiled as `self.f()`**, so inside an override it called itself.
- **Two things were missing from the model:**
  - a sub-component whose type was declared later in the source;
  - qualified core-library calls (`addr_reg_pkg::write32(...)`).

---

Sources: probes and tests in `tests/progseq/` (`test_op_model_inherit_native.py`, `test_op_model_py_inherit.py`, `test_param_shadows_field.py`), and generated output inspected directly. Items marked "re-verify" come from an earlier session and haven't been re-checked since.
