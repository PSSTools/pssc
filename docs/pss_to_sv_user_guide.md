# PSS to SystemVerilog User Guide

## Quick Start

### From Python

```python
from zuspec.fe.pss import generate_sv

files = generate_sv("""
    component top_c {
        action hello {
            exec body { }
        }
    }
""", output_dir="./sv_out")

print("Generated:", [f.name for f in files])
```

### From PSS files

```python
from zuspec.fe.pss import generate_sv_files

files = generate_sv_files(
    ["dma.pss", "top.pss"],
    output_dir="./sv_out",
)
```

### Compiling and simulating

```bash
# VCS
vcs -sverilog -f sv_out/zsp_filelist.f -o simv && ./simv

# Questa/ModelSim
vlog -f sv_out/zsp_filelist.f && vsim -c zsp_test_top -do "run -all"

# Xcelium
xrun -f sv_out/zsp_filelist.f

# Verilator (compile check only -- limited class support)
verilator --sv --lint-only -f sv_out/zsp_filelist.f
```

---

## API Reference

### generate_sv()

```python
def generate_sv(
    pss_text: str,
    output_dir: str,
    *,
    multi_file: bool = True,
    comp_type: str = None,
    root_action_type: str = None,
    import_if_type: str = None,
    import_if_driver: str = None,
    watchdog_ns: int = 0,
) -> List[Path]
```

Parse PSS source text and generate SystemVerilog files.

**Parameters:**
- `pss_text` -- PSS source as a string.
- `output_dir` -- Directory to write generated files.
- `multi_file` -- When True (default), output is split into separate files per category. When False, all SV is written to a single `zsp_pkg.sv`.
- `comp_type` -- Top-level component class name (enables top module generation).
- `root_action_type` -- Root action class name (enables top module generation).
- `import_if_type` -- Import interface class name for the top module.
- `import_if_driver` -- Driver class name to instantiate in the top module.
- `watchdog_ns` -- Deadlock watchdog timeout in nanoseconds (0 disables).

**Returns:** List of `Path` objects for all written files.

### generate_sv_files()

Same as `generate_sv()` but accepts a list of `.pss` file paths instead of inline text.

---

## Output File Descriptions

| File | Contents |
|------|----------|
| `zsp_rt_pkg.sv` | Runtime library: base classes (`zsp_component`, `zsp_action`, `zsp_resource`, etc.), parameterized pools, trace macros |
| `zsp_pkg.sv` | Enums, struct/buffer/stream/state classes, forward declarations |
| `zsp_import_if.sv` | Virtual classes with `pure virtual` task/function declarations for each component's import functions |
| `zsp_components.sv` | Component class hierarchy with constructors, sub-component fields, pool fields |
| `zsp_actions.sv` | Action classes with `rand` fields, constraints, `body()` tasks |
| `zsp_activities.sv` | Compound action activity tasks (when separated from actions) |
| `zsp_top.sv` | Top-level `module zsp_test_top` with component construction, root action execution, seed control |
| `zsp_filelist.f` | Simulator file list in compilation order |

The file list (`zsp_filelist.f`) lists files in dependency order: runtime first, then types, imports, components, actions, and finally the top module.

---

## Import Function Implementation

PSS `import` functions declare interfaces that the generated SV code calls but does not implement. You must provide an implementation class.

### PSS source

```pss
component dma_c {
    function void do_transfer(bit[32] src, bit[16] len);
}
```

### Generated virtual class (in `zsp_import_if.sv`)

```systemverilog
virtual class dma_c_import_if;
    pure virtual task do_transfer(input bit [31:0] src, input bit [15:0] len);
endclass
```

### Your implementation

```systemverilog
class my_dma_driver extends dma_c_import_if;
    virtual task do_transfer(input bit [31:0] src, input bit [15:0] len);
        // Drive DUT signals here
        @(posedge clk);
        dut.src_addr <= src;
        dut.length <= len;
        dut.start <= 1;
        @(posedge clk);
        dut.start <= 0;
        wait(dut.done);
    endtask
endclass
```

### Wiring in the top module

Pass `import_if_driver="my_dma_driver"` and `import_if_type="dma_c_import_if"` to `generate_sv()`, or manually wire it in a custom top module.

---

## Seed Control and Reproducibility

The generated top module reads a seed from the simulator command line:

```bash
./simv +zsp_seed=12345
```

If `+zsp_seed` is not provided, the default seed is 42. The seed is passed to `$urandom()` for reproducible randomization. All `std::randomize()` calls use the simulator's built-in seeding, which is controlled by the simulator's own seed mechanisms.

---

## Trace Verbosity Control

The runtime library provides trace macros controlled by `zsp_rt_verbosity`:

| Level | Output |
|-------|--------|
| 0 | Silent -- no trace output |
| 1 | Action traversals (`ZSP_TRACE_ACTION`) |
| 2 | Resource operations (`ZSP_TRACE_RESOURCE`) |

Set at simulation time:

```bash
./simv +zsp_verbosity=2
```

Or set in the generated code / your testbench:

```systemverilog
zsp_rt_pkg::zsp_rt_verbosity = 2;
```

---

## Simulator Compatibility

| Simulator | Status | Notes |
|-----------|--------|-------|
| VCS | Supported | Full `std::randomize`, `fork/join`, `mailbox` support |
| Questa | Supported | Full support |
| Xcelium | Supported | Full support |
| Verilator | Limited | No `std::randomize` or `mailbox`; compile-check only |
| Icarus Verilog | Limited | No class support; not usable for generated code |

The generated SV uses IEEE 1800-2017 features: classes with `rand` fields, `constraint` blocks, `fork`/`join`, `mailbox`, `semaphore`, and `std::randomize`.

---

## UVM Integration

For UVM-based environments, wrap the generated code in a UVM test:

```systemverilog
class zsp_uvm_test extends uvm_test;
    `uvm_component_utils(zsp_uvm_test)

    function new(string name, uvm_component parent);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        phase.raise_objection(this);

        my_dma_driver drv = new("drv", this);
        dma_c_import_if imp = new();
        pss_top pss = new("pss", null);
        pss.import_if = imp;

        begin
            root_action_t root = new();
            root.comp = pss;
            root.pre_solve();
            if (!root.randomize()) $fatal(1, "randomize failed");
            root.post_solve();
            root.activity();
        end

        phase.drop_objection(this);
    endtask
endclass
```

The generated files can be added to your UVM compilation alongside your existing testbench. The `zsp_test_top` module can be excluded when using UVM -- your UVM test replaces it.

---

## Supported PSS Features

The table below summarises every PSS construct and its current support status in the SV backend.

**Legend:** ✅ Fully supported · 🔶 Partial (see note) · ❌ Not yet supported

### Data types

| PSS construct | SV mapping | Status |
|---|---|---|
| `int` / `bit[N]` / `bool` | `int` / `bit [N-1:0]` / `bit` | ✅ |
| `string` | `string` | ✅ |
| `chandle` | `chandle` | ✅ |
| `enum E { ... }` | `typedef enum { ... } E` | ✅ |
| `struct S { ... }` | `class S` with rand fields + constraints | ✅ |
| `list<T>` / `array<T,N>` | `T [$]` / `T [N]` | ✅ |
| `map<K,V>` / `set<T>` | `V [K]` / `bit [T]` (assoc arrays) | ✅ |

### Flow objects

| PSS construct | SV mapping | Status |
|---|---|---|
| `buffer B { ... }` | `class B extends zsp_buffer` | ✅ |
| `stream S { ... }` | `class S extends zsp_stream` | ✅ |
| `state T { ... }` | `class T extends zsp_state` | ✅ |
| `output F f` on action | rand field; captured after `body()` | ✅ |
| `input F f` on action | field injected before `pre_solve()` | ✅ |
| `pool [N] T p; bind p *;` | `zsp_resource_pool #(T,N) p` on component | ✅ |
| Constraint back-propagation through binds | SV `randomize() with { }` injection | ✅ |

### Resources

| PSS construct | SV mapping | Status |
|---|---|---|
| `resource R { ... }` | `class R extends zsp_resource` | ✅ |
| `pool [N] R p; bind p *;` | `zsp_resource_pool #(R,N) p` on component | ✅ |
| `lock R r` | `rand int unsigned r_instance_id`; `p.lock(id)` / `p.unlock(id)` | ✅ |
| `share R r` | `rand int unsigned r_instance_id`; `p.try_share(id)` / `p.unshare(id)` | ✅ |
| Head-action coordinated solve for parallel blocks | Fisher-Yates shuffle or `std::randomize unique` | ✅ |

### Components

| PSS construct | SV mapping | Status |
|---|---|---|
| `component C { ... }` | `class C extends zsp_component` | ✅ |
| Component sub-component fields | field + `new()` call in constructor | ✅ |
| `function` (import) | `pure virtual task/function` in `_import_if` class | ✅ |
| Component inheritance | `class C extends Base` | ✅ |

### Actions

| PSS construct | SV mapping | Status |
|---|---|---|
| `action A { ... }` | `class A extends zsp_action` | ✅ |
| `rand` fields + constraints | SV `rand` fields + `constraint` blocks | ✅ |
| Named constraints | Named `constraint` blocks; inherited by derived types | ✅ |
| Named constraint override | Override block of same name in derived class | ✅ |
| `abstract action A` | `virtual class A extends zsp_action` | ✅ |
| Action inheritance | `class Derived extends Base` | ✅ |
| `exec body { ... }` | `virtual task body()` | ✅ |
| `exec pre_solve { ... }` | `virtual function void pre_solve()` | ✅ |
| `exec post_solve { ... }` | `virtual function void post_solve()` | ✅ |
| `covergroup in action` | SV `covergroup` + `cg_inst` field; sampled in `post_solve` | ✅ |

### Activity

| PSS construct | SV mapping | Status |
|---|---|---|
| Sequential block `{ ... }` | `begin ... end` | ✅ |
| Named action handle traversal `h;` | `h.comp=comp; h.pre_solve(); h.randomize(); ...` | ✅ |
| Anonymous traversal `do T;` | `T _anon = new(); ...` | ✅ |
| Inline constraint `do T with { ... }` | `randomize() with { ... }` | ✅ |
| `parallel { ... }` | `fork ... join` | ✅ |
| `parallel join_first/join_none { ... }` | `fork ... join_any` / `fork ... join_none` | 🔶 IR supported; pssparser gap |
| `schedule { ... }` | Topologically ordered staged fork/join | ✅ |
| `bind` in schedule | Flow-object inject/capture wiring | ✅ |
| `repeat (N) { ... }` | `repeat (N) begin ... end` | ✅ |
| `repeat (i: N) { ... }` | `for (int i=0; i<N; i++) begin ... end` | ✅ |
| `do-while` / `while-do` | `do ... while` / `while ... begin ... end` | ✅ |
| `foreach` | `foreach (coll[iter]) begin ... end` | ✅ |
| `if`/`else` | `if ... begin ... end else begin ... end` | ✅ |
| `match` | `case ... endcase` | ✅ |
| `select { [w]: ... }` | Weighted random branch (shuffle or `std::randomize`) | ✅ |
| `replicate (N) { ... }` | `fork` with loop over body | ✅ |
| `replicate (i: N) { ... }` | `fork` with indexed loop | ✅ |
| `atomic { ... }` | Semaphore `get(1)` / `put(1)` guard | ✅ |
| `super` traversal | `super.activity()` | ✅ |

### Exec-body statements

| PSS construct | SV mapping | Status |
|---|---|---|
| Assignment `x = expr` | `x = expr;` | ✅ |
| Augmented assignment `x += expr` | `x += expr;` | ✅ |
| `if`/`else`, `match`, `foreach` | Standard SV control flow | ✅ |
| `repeat`, `while`, `do-while` | Standard SV loops | ✅ |
| `break` / `continue` | `break` / `continue` | ✅ |
| `return` | `return` | ✅ |
| Function call | Direct SV call | ✅ |
| `message(verbosity, fmt, ...)` | `$display(fmt, ...)` | ✅ |
| `yield` | Comment (no SV equivalent in class context) | 🔶 |
| `cover(expr)` | `cover (expr);` + `ZSP_TRACE` | ✅ |
| `assert(expr)` / `assume(expr)` | `assert (expr);` / (comment) | ✅ / 🔶 |

---

## Flow-Object Support

PSS flow objects — **buffer**, **stream**, and **state** — carry data between producer and consumer actions. The SV backend handles all three.

### Buffer objects

A buffer is produced by one action, lives in a pool between actions, and consumed by a later action. In generated SV, the buffer is materialised as a class instance stored in a local variable between traversals.

```pss
buffer packet_buf { rand int seq; }

component pss_top {
    pool packet_buf pkt_pool;  bind pkt_pool *;

    action produce { output packet_buf out_pkt; }
    action consume { input  packet_buf in_pkt; }

    action do_test {
        activity {
            p: do produce;
            c: do consume;
            bind p.out_pkt c.in_pkt;
        }
    }
}
```

Generated SV (activity task, simplified):

```systemverilog
task automatic activity();
    packet_buf _flow_p_out_pkt;      // buffer local variable

    // Producer
    produce p = new();
    p.comp = comp;
    p.pre_solve();
    if (!p.randomize()) $fatal(1, "randomize failed: p");
    p.post_solve();
    p.body();
    _flow_p_out_pkt = p.out_pkt;     // capture after body

    // Consumer — injected value pins the constraint
    consume c = new();
    c.in_pkt = _flow_p_out_pkt;      // inject before pre_solve
    c.comp = comp;
    c.pre_solve();
    if (!c.randomize() with { in_pkt == _flow_p_out_pkt; })
        $fatal(1, "randomize failed: c");
    c.post_solve();
    c.body();
endtask
```

The consumer's `randomize() with { in_pkt == _flow_p_out_pkt; }` clause is the **constraint back-propagation**: any constraints the consumer places on `in_pkt` fields are visible to the SV solver and can influence what the producer generates.

### Stream objects

A stream is like a buffer but carries timing semantics: the producer and consumer execute concurrently (in a `schedule` block), and the stream value flows from producer to consumer via a typed channel.  The generated SV wiring is the same as for buffers — a local variable threaded through the activity.

### State objects

A state object represents persistent shared state that can be read and written by multiple actions. Unlike buffers, a state object is not consumed: the same state object passes in and out of each action. This models registers or flag sets that accumulate mutations over a scenario.

```pss
state link_state_s { rand bool established; rand int retry_count; }

action link_connect {
    input  link_state_s prev;
    output link_state_s next;
    constraint next.established == true;
}
```

The generated SV is structurally identical to buffer wiring: inject `prev` before `pre_solve`, capture `next` after `body`.

### Constraint back-propagation

When a consumer places a constraint directly on an input flow-object field, that constraint is visible to the SV solver through the `randomize() with {}` clause. Consider:

```pss
action pipe_end {
    input image_stream final_istate;
    constraint final_istate.resized == true;   // consumer constraint
}
```

The generated consumer randomize call includes this constraint as a with-clause, which forces the upstream producer (via the injected flow variable) to also satisfy it. For simple SV-solvable cases this happens automatically. For complex cross-action chains (where the constraint involves multiple linked actions), the DPI joint-chain solver is used instead — see **Schedule Blocks and Joint Chain Solve** below.

---

## Schedule Blocks

A PSS `schedule` block declares a set of actions with partial ordering determined by `bind` directives and flow-object data dependencies. The SV backend topologically stages the schedule into `fork`/`join` groups.

```pss
action do_pipeline {
    activity {
        schedule {
            a0: do load;
            a1: do process;
            a2: do store;
            bind a1.in_buf a0.out_buf;
            bind a2.in_buf a1.out_buf;
        }
    }
}
```

Generated SV (simplified):

```systemverilog
// Stage 1: load (no dependencies)
begin
    load a0 = new();
    ...
    a0.body();
    _flow_a0_out_buf = a0.out_buf;
end

// Stage 2: process (depends on a0 output)
begin
    process a1 = new();
    a1.in_buf = _flow_a0_out_buf;
    ...
    a1.body();
    _flow_a1_out_buf = a1.out_buf;
end

// Stage 3: store (depends on a1 output)
begin
    store a2 = new();
    a2.in_buf = _flow_a1_out_buf;
    ...
    a2.body();
end
```

Actions that are independent of each other within a schedule block are placed in the same stage and executed as a `fork`/`join`.

### Joint chain DPI solve

When a pipeline chain has cross-action constraints that cannot be satisfied by injecting solved values action-by-action (for example, a constraint on the chain exit that requires the solver to see all intermediate variables simultaneously), the backend falls back to the **joint chain DPI solve** protocol:

1. At generation time, all chain variables and constraints are compiled into a single `SolveProblem` and base64-encoded as a package constant (`CHAIN_PROBLEM_B64`).
2. At simulation time:
   - `zsp_dpi_compile_b64(CHAIN_PROBLEM_B64)` — deserialise the problem once.
   - `zsp_dpi_pin_var_h(ctx, VID_x, value)` — pin chain entry variables from upstream buffers.
   - `zsp_dpi_solve_h(ctx, $urandom())` — single joint solve of the entire chain.
   - `zsp_dpi_get_value_h(ctx, VID_y)` — read solved values back into action fields.
   - Execute actions in chain order: `pre_solve` / `post_solve` / `body`.
   - `zsp_dpi_release_h(ctx)` — release the solver context.

This is the path that makes the pipeline example with `resized == true` on `pipe_end` work correctly — the constraint propagates back through the entire chain in one solve step.

---

## Resource Pools

PSS resource objects represent exclusive or shared hardware resources (DMA channels, MMIO regions, mutex-protected blocks). The SV backend implements full pool lifecycle management.

### Declarations

```pss
resource dma_channel_r { rand bit[4] id; }

component dma_c {
    pool [4] dma_channel_r ch_pool;
    bind ch_pool *;

    action mem_copy {
        lock dma_channel_r ch;   // exclusive
    }
}
```

Generated component:

```systemverilog
class dma_c extends zsp_component;
    zsp_resource_pool #(dma_channel_r, 4) ch_pool;  // pool field

    function new(string name, zsp_component parent);
        super.new(name, parent);
        ch_pool = new("ch_pool", this);
    endfunction
endclass
```

Generated action:

```systemverilog
class dma_c__mem_copy extends zsp_action;
    dma_channel_r    ch;                  // resource reference
    rand int unsigned ch_instance_id;     // pool slot selector (rand)
    ...
endclass
```

### Lock/unlock lifecycle

In a sequential traversal:

```systemverilog
// after randomize() — acquire
comp.ch_pool.lock(mem_copy_inst.ch_instance_id);
mem_copy_inst.ch = comp.ch_pool.get(mem_copy_inst.ch_instance_id);

mem_copy_inst.body();

// after body() — release (reverse order)
comp.ch_pool.unlock(mem_copy_inst.ch_instance_id);
```

`share` uses `try_share` / `unshare` instead of `lock` / `unlock`.

### Parallel coordination

When a `parallel` block has branches that all claim resources from the same pool, the SV backend emits a **head-action coordinated solve** before the `fork`: it assigns unique pool slots to each branch's head action so they cannot deadlock waiting for the same resource.

For N ≤ 8 claimants, a Fisher-Yates shuffle assigns pool indices. For larger N, `std::randomize` with a `unique` constraint is used. Lock acquisition still happens in canonical alphabetical order within each branch to prevent hold-and-wait deadlocks.

---

## Coverage

PSS `covergroup` blocks inside actions are mapped to SV `covergroup` declarations.

```pss
action write_op {
    rand int addr;
    rand bool cached;
    covergroup cg_write_op {
        cp_addr:   coverpoint addr   { bins low  = {[0:0xFF]};
                                       bins high = {[0x100:0xFFFF]}; }
        cp_cached: coverpoint cached;
    }
}
```

Generated SV (inside action class):

```systemverilog
class pss_top__write_op extends zsp_action;
    rand int addr;
    rand bit cached;

    covergroup cg_pss_top__write_op;
        cp_addr:   coverpoint addr;
        cp_cached: coverpoint cached;
    endgroup

    pss_top__write_op cg_inst;

    virtual function void post_solve();
        if (cg_inst != null) cg_inst.sample();
    endfunction
    ...
endclass
```

The covergroup is sampled automatically in `post_solve()` after every `randomize()` call, so every generated test stimulus contributes to coverage.

The PSS `cover` statement in exec blocks is mapped to a procedural `cover` assertion plus a `ZSP_TRACE` call for observability:

```systemverilog
`ZSP_TRACE("cover: my_point");
cover (condition_expr);
```

---

## Register Models (`addr_reg_pkg`)

`sv-native` lowers `reg_c<R, ACC, SZ>` / `reg_group_c` using the shared runtime
`pssc_reg_pkg.sv` (also used by `sv-progseq`).

- **Types.** Each register is a `reg_c #(R, ACC, SZ)` (user subtypes extend it);
  each register group becomes a class built with `new(pss_mem_if bus, base)`.
  Struct-typed `R` is packed/unpacked LSB-first (first field at bit 0).
- **Access.** `r.write_val(v)`, `r.write(s)`, `x = r.read_val()` and `s = r.read()`
  lower to `reg_c` tasks (`write_val(bit[SZ-1:0])`, `write(R)`,
  `read_val(output bit[SZ-1:0])`, `read(output R)`).
- **Addresses.** `regs.set_handle(addr)` in a component `exec init_down` sets the
  group base. Offsets come from `get_offset_of_instance()` /
  `get_offset_of_instance_array()` and are resolved at compile time; nested groups
  and several instances of one group type are supported.
- **Bus hooks.** When a model uses registers, `import_api_if` gains:

  ```systemverilog
  pure virtual task reg_write(bit [63:0] addr, bit [63:0] data, int size);
  pure virtual task reg_read(bit [63:0] addr, output bit [63:0] data, int size);
  ```

  `size` is in bytes; `data` is always 64 bits wide (low `size*8` bits used).
  A generated `pss_reg_bus_adapter` (implements `pss_mem_if`) forwards every
  register access to these hooks; `create(imp_if)` binds it to `imp_if`.
  `import_api_base` stubs them with `$fatal`, so override both.
- **Access modes.** A write to a `READONLY` register or a read of a `WRITEONLY`
  register reports `$error` at run time and skips the bus access. (No
  compile-time diagnostic yet.)

Known gaps:

- A register read must be the whole right-hand side of an assignment; a read
  inside a larger expression, or a discarded `r.read_val();`, is not lowered.
- Register groups in a nested sub-component get an adapter that is not bound to
  `imp_if` (access fails with `$fatal`); put groups on the root component.
- The legacy (non-OO API) projection does not emit `pss_reg_bus_adapter`.
- Icarus 12 cannot compile the generated `zsp_rt_pkg.sv` (`semaphore`); use Verilator.

---

## Address Spaces and Claims

`sv-native` lowers `transparent_addr_space_c`, `transparent_addr_claim_s` and
`addr_handle_t` operations from `addr_reg_pkg`. The runtime classes live in
`pssc_reg_pkg.sv`, which is copied into the output when a model uses them.

- **Address spaces.** A `transparent_addr_space_c<>` component field becomes a
  runtime `transparent_addr_space_c` (`add_region(r)`, `alloc(size, align, out addr)`,
  `claim(c)`, `free(addr)`). Regions are added in a component `exec init_down`:

  ```pss
  exec init_down {
      transparent_addr_region_s<> r;
      r.addr = 0x0; r.size = 0x1000;
      (void)aspace.add_region(r);
  }
  ```

  `exec init_down` (locals, field assigns, calls) is lowered into the component
  constructor, after its fields are built.
- **Claims.** Each `rand transparent_addr_claim_s<>` field of an action is
  allocated in `post_solve()` (first-fit, aligned to the solved `alignment`, sized
  by the solved `size`) from the component's first `transparent_addr_space_c`
  field, and freed at the end of the action's `body()`.
- **Claims in buffer objects.** For `buffer b { rand transparent_addr_claim_s<> c; }`
  the producer claims `o.c` in `post_solve()` and the consumer frees `i.c.addr` after
  its body, so the consumer sees the producer's address. The flow binding must be
  explicit: `bind p.o c.i;`.
- **Handles.** `addr_handle_t` is `bit [63:0]`. `make_handle_from_claim(c[, off])`,
  `make_handle_from_handle(h, off)` and `addr_value(h)` are lowered to address
  arithmetic.
- **Memory access.** `write8/16/32/64(h, v)` and `v = read8/16/32/64(h)` go through
  two hooks that `import_api_if` gains when address spaces are used:

  ```systemverilog
  pure virtual task mem_write(input bit [63:0] addr, input bit [63:0] data, input int size);
  pure virtual task mem_read(input bit [63:0] addr, output bit [63:0] data, input int size);
  ```

  `size` is in bytes (1/2/4/8); `data` is 64 bits wide (low `size*8` bits used).
  `import_api_base` stubs both with `$fatal`, so override both.

Known gaps:

- A `readN()` must be the whole right-hand side of an assignment; a read inside a
  larger expression is not lowered.
- Buffer flow objects between sequential `do`s are not bound implicitly; without
  `bind p.o c.i;` the consumer's input is null.
- With several consumers of one buffer, the first to finish frees the claim.
- `exec init_down` runs in the constructor, so a child component's runs before its
  parent's (PSS order is parent first).
- Memory accesses always go to the root component's `import_if`.

---

## Constraint Solving Strategy

The backend uses a three-tier strategy, applied per action based on what types of fields and constraints are present:

**SV-native** — The action has no flow-object input fields and no cross-action constraints. All fields are solved entirely by the SV `randomize()` call. This is the fast path and requires no Zuspec DPI involvement at simulation time.

**SV with injection** — The action consumes a flow-object whose values were determined by an upstream producer. The solved values are injected as constants before `pre_solve()` and pinned via `randomize() with {}`. The SV solver handles everything; DPI is not needed.

**DPI joint-chain** — One or more cross-action constraints cannot be satisfied action-by-action. The backend identifies the chain at generation time, compiles the full chain problem, embeds it as a base64 constant, and emits the five-step DPI protocol at simulation time. This is used for pipelines where exit constraints back-propagate through intermediate transformations.

The classification is computed during the analysis pre-pass in `pss_to_sv()` and stored in the lowering context. No runtime overhead is paid for the SV-native or SV-with-injection tiers.

---

## Generation-Time Diagnostics

The backend prints warnings to stderr for constructs that are partially supported or silently degraded:

```
zuspec-sv warning [NodeType]: unsupported activity node 'X' — skipped
```

Warnings are also accessible programmatically via the `LoweringContext.warnings` list after the lowering pass returns. This list can be inspected in unit tests or build scripts to detect unexpected degradation.

Errors that prevent any SV from being generated (IR translation failures, file I/O errors) raise Python exceptions as normal.

---

## VCS-Specific Notes

These behaviours were discovered during VCS W-2024.09 validation and
influenced the generated code style. Other simulators (Questa, Xcelium) do not
share them.

### Output flow-object fields must be `rand`

VCS treats handles that are NOT declared `rand` as fixed-value constants in the
constraint solve. If `next` is a non-`rand` `link_state_s` handle, the
constraint `next.established == 1` causes `CNST-CIF` immediately — the solver
sees `established = 0` (default) as a constant conflicting with the constraint.

The backend declares every `output` flow-object field as `rand` so VCS
includes its sub-fields in the constraint scope.

### `rand_mode(0)` applies class-wide in VCS

Calling `some_obj.rand_mode(0)` where `some_obj` is of type `T` disables
randomization of **all** `T` instances, not just `some_obj`. The backend avoids
calling `rand_mode` on flow-object handles for this reason. `constraint_mode`
is used instead (it is per-instance).

### Compound action constraint scoping

When a compound action (one with named sub-action handles) calls
`do_test.randomize()`, VCS traverses sub-action constraint graphs. If a
sub-action has constraints that reference flow-object inputs (e.g. `prev`),
which are null until `body()` runs, the traversal causes `CNST-CIF`.

The fix: call `handle.constraint_mode(0)` in the parent's `pre_solve()`,
then `handle.constraint_mode(1)` before each explicit `handle.randomize()` in
`body()`.

### Named begin/end blocks for local variable declarations

Array and scalar declarations inside **unnamed** `begin`/`end` blocks in
`virtual task` bodies are rejected by VCS (even with `automatic`). The
`select` weighted-branch lowering uses a named block `begin : _zsp_sel` to
work around this.

### `randomize()` on compound root actions

For compound actions with no direct `rand` fields, the generated test harness
skips `root.randomize()` entirely (the sub-actions are randomized individually
in `body()`). Calling `randomize()` on such an action would traverse the null
flow-input graph of sub-actions and produce `CNST-CIF`.

