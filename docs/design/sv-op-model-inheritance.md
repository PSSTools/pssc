# SV Op-Model: Native Inheritance

Status: proposal for discussion, 2026-09-24. Revised after review (MSB): no interface classes, `imp` naming, the `initialize` convention, and a factory class (`pss_top_root`) separate from the component tree. Revised again (MSB): a two-level export API — a per-executor export API, and a root API that adds executor lookup and the exported actions.

Related: [Op-Model Output Defects](op-model-output-defects.md). The relevant items are:
- SV-1: inheritance is flattened.
- SV-2: value-returning calls are not hoisted.
- SV-3: sub-components are not constructed.
- SV-4: `repeat` is not lowered.

## Why

`op-model-sv` renders a derived component by **flattening**. Every inherited member is copied into the derived class, and `super.f(...)` becomes a call to a private copy of the base's `f`. For SystemVerilog that is categorically wrong, even apart from the task-call restriction:

- A derived component's class is unrelated to its base's, so a `fast_dev_c` cannot be used where a `dev_base_c` is expected.
- The generated classes cannot be extended or overridden in SV.
- The base's code is duplicated in every derived class.
- pssc's private names (`_pss_super_<base>_<f>`) appear in the generated API.

op-model-py (`class Der(Base)`, `super()`) and op-model-cpp (`class der : public base`, `base::f`) already render inheritance natively. This document works out the equivalent shape for SV on one example, and records the decisions it needs.

## The example

This model is small but exercises every inheritance mechanism SV-1 involves:
- a field, a register block and a constructor in the base;
- a base function (`start`) that calls a function the derived component overrides, so the call is virtual;
- an override that calls `super`;
- a field the derived component declares again.

It also has one export function (`poke`) and one exported action (`entry_a`), the two things the platform can call.

It follows the op-model constructor convention (D3): a component's `initialize` is called by its containing component's `exec init_down`, and the root's by the platform.

It avoids value-returning calls inside expressions, so SV-2 doesn't get in the way. Its loop is a `while` rather than a `repeat` because of SV-4.

### PSS

```pss
import std_pkg::*;
import addr_reg_pkg::*;

pure component dev_regs_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTRL;
  reg_c<bit[32], READWRITE, 32> STAT;
  function bit[64] get_offset_of_instance(string name) {
    match (name) {
      ["CTRL"]: return 0x0;
      ["STAT"]: return 0x4;
    }
    return 0;
  }
}

component dev_base_c {
  dev_regs_c regs;
  int        retries = 3;

  // Op-model constructor: called by the containing component's init_down.
  solve function void initialize(addr_handle_t base) {
    regs.set_handle(base);
  }

  target function void start(bit[32] mode) {
    regs.CTRL.write_val(mode);
    wait_idle();                          // virtual: fast_dev_c's on a fast_dev_c
  }

  target function void wait_idle() {
    int n = 0;
    while (n < retries) {                 // always dev_base_c::retries
      regs.STAT.read_val();
      n += 1;
    }
  }
}

component fast_dev_c : dev_base_c {
  int retries = 10;                       // a second field, not a replacement

  target function void wait_idle() {
    super.wait_idle();
    regs.CTRL.write_val(0);
  }
}

component pss_top {
  dev_base_c    slow;
  fast_dev_c    fast;
  addr_handle_t base;

  // The root's constructor is called by the platform (create()). Its arguments
  // reach init_down through a field.
  solve function void initialize(addr_handle_t b) {
    base = b;
  }

  exec init_down {
    slow.initialize(base);
    fast.initialize(base + 0x100);
  }

  action entry_a {
    exec body {
      comp.slow.start(1);
      comp.fast.start(2);
    }
  }
}

// An export function: static, called by the platform in an executor's context.
function void poke(addr_handle_t addr, bit[32] data) {
  write32(addr, data);
}

export target function poke;
export target pss_top::entry_a();
```

Expected behaviour of `entry_a()`:
- `slow.start(1)` writes CTRL and reads STAT 3 times.
- `fast.start(2)` writes CTRL and reads STAT **3** times: `super.wait_idle()` runs `dev_base_c`'s code, which reads the base's `retries`. It then writes CTRL = 0.

### What op-model-sv generates today (flattened)

This was generated from an earlier version of the example whose root called `slow.initialize(base)` from its own `initialize` rather than from `init_down`. op-model-sv refuses `exec init_down` today, so it has no output for the version above. Trimmed:

```systemverilog
interface class fast_dev_c_if;
  pure virtual task start(input bit [31:0] mode);
  pure virtual task _pss_super_dev_base_c_wait_idle();
  pure virtual task wait_idle();
endclass

class fast_dev_c implements fast_dev_c_if;       // no relation to dev_base_c
  protected pss_top_import_if m_imp;
  protected dev_regs_c m_regs;
  protected int m__pss_super_dev_base_c_retries;
  protected int m_retries;

  function new(pss_top_import_if bus, addr_handle_t base);   // PSS ctor folded in
    m_imp = bus;
    m__pss_super_dev_base_c_retries = 3;
    m_retries = 10;
    m_regs = new(m_imp, base);
  endfunction

  virtual task start(input bit [31:0] mode);     // copy of dev_base_c::start
    m_regs.CTRL.write_val(mode);
    wait_idle();
  endtask

  virtual task _pss_super_dev_base_c_wait_idle(); // copy of dev_base_c::wait_idle
    int n = 0;
    while (n < m__pss_super_dev_base_c_retries) begin
      m_regs.STAT.read_val();
      n += 1;
    end
  endtask

  virtual task wait_idle();
    _pss_super_dev_base_c_wait_idle();
    m_regs.CTRL.write_val(0);
  endtask
endclass

class pss_top #(type IMP_T = pss_top_import_if) implements pss_top_if, pss_top_import_if;
  ...
  function new(IMP_T imp, addr_handle_t base);
    m_imp = imp;
    m_slow = new(this, base);                    // `slow.initialize(base)` became construction
    m_fast = new(this, base + 256);
  endfunction
```

### Proposed native shape

This is a proposal, not generated output.

```systemverilog
// ----- Import API: what the platform supplies -----
interface class pss_top_imp_if extends pss_mem_if;
  // ...plus the model's `import` functions, if any
endclass

// ----- Export API, level 1: one executor's context (D9, D13) -----
// A handle to this IS the executor context: the object-oriented form of the
// opaque chandle executor_base_c::get_context() returns. It carries the model's
// export functions, each run in that executor's context.
interface class pss_top_exp_if;
  pure virtual task poke(addr_handle_t addr, bit [31:0] data);
endclass

// ----- Export API, level 2: the root context (D9, D11, D12) -----
// The root executor's export functions, plus executor lookup and the exported
// actions, each by its own name.
interface class pss_top_ctxt_if extends pss_top_exp_if;
  // Map the opaque handle from get_context() to that executor's export API.
  pure virtual function pss_top_exp_if get_context(chandle ctxt);

  pure virtual task entry_a();
endclass

// ----- Common base of every component class (D8) -----
virtual class pss_top_component;
  protected pss_top_imp_if m_imp;

  function new(pss_top_imp_if imp);
    m_imp = imp;
  endfunction

  // PSS construction, LRM 20.1.2: this component's init_down, then every
  // sub-component's, then its init_up. Each hook is overridden only by a class
  // that declares that exec kind or adds sub-components (D4).
  virtual function void pss_init_down(); endfunction
  virtual function void pss_init_subs(); endfunction
  virtual function void pss_init_up();   endfunction

  function void pss_do_init();
    pss_init_down();
    pss_init_subs();
    pss_init_up();
  endfunction
endclass

// ----- dev_base_c -----
class dev_base_c extends pss_top_component;
  protected dev_regs_c m_regs;
  protected int m_retries;

  // Construction: fields at their defaults, sub-components built, register
  // groups at address 0 (D2).
  function new(pss_top_imp_if imp);
    super.new(imp);
    m_retries = 3;
    m_regs = new(m_imp, 0);
  endfunction

  // Op-model constructor: called by the containing component's init_down.
  // Not virtual (D3).
  function void initialize(addr_handle_t base);
    m_regs = new(m_imp, base);
  endfunction

  virtual task start(input bit [31:0] mode);
    m_regs.CTRL.write_val(mode);
    wait_idle();                                 // virtual dispatch, natively
  endtask

  virtual task wait_idle();
    int n = 0;
    while (n < m_retries) begin                  // dev_base_c::m_retries, statically
      m_regs.STAT.read_val();
      n += 1;
    end
  endtask
endclass

// ----- fast_dev_c -----
class fast_dev_c extends dev_base_c;
  protected int m_retries;                       // hides dev_base_c::m_retries (D6)

  function new(pss_top_imp_if imp);
    super.new(imp);
    m_retries = 10;
  endfunction

  // initialize() is inherited: fast_dev_c declares no constructor.

  virtual task wait_idle();
    super.wait_idle();                           // D7
    m_regs.CTRL.write_val(0);
  endtask
endclass

// ----- pss_top: the root of the component tree, nothing more -----
class pss_top extends pss_top_component;         // not parameterized, not the API
  protected dev_base_c m_slow;
  protected fast_dev_c m_fast;
  protected addr_handle_t m_base;                // the PSS field `base`

  function new(pss_top_imp_if imp);
    super.new(imp);
    m_slow = new(m_imp);                         // every instance built here (fixes SV-3)
    m_fast = new(m_imp);
  endfunction

  function void initialize(addr_handle_t b);
    m_base = b;
  endfunction

  virtual function void pss_init_down();
    m_slow.initialize(m_base);                   // the model's own calls, as written
    m_fast.initialize(m_base + 64'h100);
  endfunction

  virtual function void pss_init_subs();
    m_slow.pss_do_init();
    m_fast.pss_do_init();
  endfunction

  // The body of action entry_a, run with this component as its `comp` (D12).
  task pss_action_entry_a();
    m_slow.start(1);
    m_fast.start(2);
  endtask
endclass

// ----- pss_top_root: the factory (D5, D9) -----
// Constructs the component tree and is the root context the platform holds. It
// is also the tree's import API: it forwards every import call to the platform
// object. The only class that knows the platform's type.
class pss_top_root #(type Timp = pss_top_imp_if) implements pss_top_imp_if, pss_top_ctxt_if;
  protected Timp    m_imp;                       // the platform object
  protected pss_top m_top;                       // the component tree

  protected function new(Timp imp);              // only create() builds a model
    m_imp = imp;
    m_top = new(this);                           // the tree reaches the platform through this
  endfunction

  static function pss_top_ctxt_if create(Timp imp, addr_handle_t base);
    pss_top_root #(Timp) model = new(imp);
    model.m_top.initialize(base);                // the platform is the root's container
    model.m_top.pss_do_init();
    return model;
  endfunction

  // Executor lookup. This model declares no executor components, so every
  // context is the root's (D13).
  virtual function pss_top_exp_if get_context(chandle ctxt);
    return this;
  endfunction

  // Export functions, run in the root executor's context.
  virtual task poke(addr_handle_t addr, bit [31:0] data);
    m_imp.write32(addr, data);
  endtask

  // Exported actions, by name. entry_a is matched with a pss_top context; there
  // is one, so there is no choice to make (D12).
  virtual task entry_a();
    m_top.pss_action_entry_a();
  endtask

  // Import API, forwarded to the platform.
  virtual task write32(addr_handle_t addr, bit [31:0] data); m_imp.write32(addr, data); endtask
  virtual task read32(addr_handle_t addr, output bit [31:0] data); m_imp.read32(addr, data); endtask
  // ...the other primitives and imports
endclass
```

A user writes:

```systemverilog
my_platform     plat = new();
pss_top_ctxt_if root = pss_top_root #(my_platform)::create(plat, 64'h4000_0000);
root.entry_a();
root.poke(64'h4000_0200, 32'h1);
```

`my_platform` needs only the methods `pss_top_imp_if` declares. It does not have to implement the interface: `Timp` is checked when `pss_top_root #(my_platform)` is specialized. A platform class that does implement it can use the default, `pss_top_root #()::create(...)`.

## Decisions

### Settled

- **D1: no per-component interface classes.**
  - A component is one SV class, and its operations are `virtual` tasks and functions.
  - A derived component's class extends its base's, so it is usable wherever the base is. Virtual dispatch and `super` are SystemVerilog's own.
  - The per-component `<comp>_if` interface classes go away, and so do the sub-component accessors they carried.
  - The interface classes that remain are the model's boundary: the import API (`pss_top_imp_if`), and the two levels of the export API (`pss_top_exp_if` and `pss_top_ctxt_if`, D9).
- **D2: construction is split from the op-model constructor.**
  - `new(imp)` builds the object: fields at their defaults, every sub-component constructed, register groups at address 0. It takes only the import API, so a derived class's `super.new(imp)` is always expressible.
  - The PSS constructor body is a separate method (D3).
  - This changes SV output for **every** model (golden snapshots `sv-named` and `sv-folded`), as D1, D5 and D9 do too. It also fixes SV-3.
- **D3: `initialize` is an op-model convention.** PSS itself gives the name no meaning; `--ctor-name` names the function.
  - A component's `initialize` is called by its containing component's `exec init_down`. The root's is called by the platform, through `create()`.
  - A component never calls its own. Every caller uses its member's declared type, so the method is **not virtual**. A derived component may then declare an `initialize` with a different signature, and `super.initialize(...)` still reaches the base's.
  - Order: a parent's `init_down` runs before any child's (LRM 20.1.2), so every component is configured before its own `init_down` runs. For the root, `create()` runs `initialize(args)` and then `pss_do_init()`.
  - This differs from what pssc does today. Python and SV treat `ch[i].initialize(...)`, called from the parent's `initialize`, as *constructing* the sub-component. The WB DMA model and the tests are written that way. All three backends change, and so do the models.
- **D4: PSS construction is a hook per step.**
  - `pss_do_init()` is defined once, in the common base class. It runs `pss_init_down()`, then `pss_init_subs()`, then `pss_init_up()`.
  - A class overrides `pss_init_down`/`pss_init_up` only if it declares that exec kind. The kinds are shadowed separately (Table 27), and `super;` in one is `super.pss_init_down()`.
  - A class overrides `pss_init_subs` only if it adds sub-components, and calls `super.pss_init_subs()` first.
  - The `pss_` prefix keeps these clear of PSS functions: SV has one method namespace per class.
- **D5: the factory class, not the root component, is the import API.**
  - Today the root class is parameterized by `IMP_T` and *is* the import API: it forwards every primitive and passes `this` down. A derived root would then need `super.new(this)`.
    - Verilator 5.052 accepts that and runs it correctly.
    - It is unchecked against IEEE 1800 and commercial simulators.
    - It hands the base constructor an object whose own fields aren't set yet.
  - Instead, the factory class `pss_top_root #(Timp)` implements `pss_top_imp_if` by forwarding to the platform object. Every component class, the root included, takes a `pss_top_imp_if`, and the factory passes itself.
  - No component class is parameterized, and the root extends its base like any other component.
- **D9: users hold a context, never a component. The export API has two levels.**
  - The component tree is internal. `pss_top_root::create(imp, <root initialize args>)` constructs the factory and the tree, runs the root's `initialize` and then `pss_do_init()`, and returns a `pss_top_ctxt_if`.
  - `pss_top_exp_if` is one executor's context. PSS identifies an executor to an export function with an opaque `chandle` from `get_context()` (LRM 20.4.2, 21.7.1.1.1), a global-function interface. This is its object-oriented projection: a handle to a `pss_top_exp_if` is both the context and the API, and its methods are the model's export functions, run in that executor's context.
  - `pss_top_ctxt_if` extends it and is the root's context: the root executor's export functions, `get_context(chandle)` to reach any other executor's `pss_top_exp_if` (D13), and the exported actions (D12).
  - The factory `<root>_root` implements `pss_top_ctxt_if`.
  - `create()`'s arguments are the root `initialize`'s parameters, as today.
- **D11: the export API is export functions and exported actions, nothing else.**
  - Target functions, the root's included, are not on it. A user reaches the device only through `export function`s and exported actions.
  - The accessors returning component handles (`dut.ch(2).start(...)` in WB DMA) go. WB DMA and its tests must gain exports for what they call today.
- **D12: an exported action is a method of `pss_top_ctxt_if`, under its own name.**
  - `export target pss_top::entry_a();` gives `task entry_a()`; the tests rely on the name. The export statement's parameters become the task's, associated by name with the action's fields (LRM 20.10).
  - This is the PSS 3.1 object-oriented projection: the action is evaluated in the context of `pss_top` and matched with a component context of its type. When more than one exists, one is chosen at random, per call (each call infers its own tree, LRM 20.10 b).
  - The body is rendered as a task on the component class the action belongs to (`pss_action_entry_a`), so `comp.x` is `x`.

### Consequences to confirm

- **D6: fields keep SV's own hiding.**
  - `fast_dev_c::m_retries` hides `dev_base_c::m_retries` rather than replacing it. That is exactly LRM 17.1: `dev_base_c`'s code keeps reading its own.
  - `super.retries` in PSS is `super.m_retries`.
  - It needs a simulation test.
- **D7: `super.f(...)` of a value-returning operation** is `super.f(status, x)`, a task call, so it may only appear as a statement. Inside an expression it needs SV-2's hoisting.
- **D8: one generated base class per model** (`pss_top_component`).
  - It holds `m_imp` and the construction hooks, so they are declared once. Every component class that has no user base extends it.
  - The alternative is repeating them in each base-most class.
- **D10: shadowing with another signature.**
  - An SV virtual override must match its base's prototype exactly, so a derived `scale()` cannot shadow a virtual `scale(int)` (LRM Example 284's shape).
  - `initialize` avoids this by not being virtual (D3). Ordinary operations are virtual, so SV should refuse the shape with a clear error.
  - C++ has the same problem today (defects list, CPP-2).

### Open

- **D13: where a `chandle` comes from in pure SV.** IEEE 1800 lets a `chandle` be only `null`, another `chandle`, or a value from DPI. So a model without DPI cannot implement PSS's `executor().get_context()`, and `get_context(chandle)` has nothing to map. The options:
  - in the SV projection, type PSS `chandle` as `pss_top_exp_if` wherever it carries an executor context, so `get_context()` returns the executor's context object and the lookup becomes the identity;
  - accept that executor lookup needs DPI.
  - The example has no executor components, so its `get_context` returns the root.
- **D14: names on `pss_top_ctxt_if`.** Its built-in methods, the export functions and the exported actions share one SV method namespace, and so does the import API, because the factory implements both. The collisions:
  - an export function or exported action named `get_context` (D4's reason for the `pss_` prefix applies);
  - two exported actions with the same short name in different components (`a_c::entry_a`, `b_c::entry_a`). The LRM separates these by SV package (20.10.3);
  - an import function and an export function or action with the same name. Moving the import forwarding into an inner object that the tree is given removes this one, and D5 still holds.
- **Naming.** `pss_top_root`, `pss_top_ctxt_if`, `pss_top_exp_if` and `pss_top_imp_if` follow the root's name, as today's `pss_top_import_if` does. Confirm those, or choose fixed names (`pss_imp_if`).

The proposed output should become a Verilator simulation test that checks the expected access trace above. The SV tests today only lint.

## Out of scope here

- **Hoisting value-returning calls (SV-2).** It is needed for `super.f(...)` inside an expression (D7) and for every other such call, and it is the same work for C coroutines.
- **C.** op-model-c keeps the flattened rendering until it gets a per-component function table (vtable).
