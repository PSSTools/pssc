
We're working on creating device programming sequences in PSS.
There will be lots of them, and we'll need to develop, test, and debug them.

We'll want to be able to do the following:
- Develop functionality against an abstract model (stub)
- Write unit tests 
- Collect code coverage

One of our test targets is a simulation image. We'll want to have the ability
to run our unit tests against the simulated design. This is very slow to compile,
so we won't want to recompile any time we change code.


## Unit-test package -- pssunit
- Actions as tests?
- Mark entrypoints with an attribute for gathering
- Component as test fixture (?)

- Environment is characterized by imports and exports
- 

extend pss_top {
    MyFixture fixture;

    exec init_down {
        fixture.api = api;
    }
}

Minimum:
- 
Setup and teardown tends to be per-test
- Initialize (?)
- 

<foreach A>

- Sim launches interpreter (specify)
- 


## Debug
- 

# Key capabilities
- internal code coverage -- filtered to the boundary we care about
- unit test framework
  - Some form of discovery (for standalone execution)
  - results checking and error reporting
- debug trace
- (ideally) interactive debug -- live and trace-based
- integration with simulation (run what you develop)
  - want an interpreter that integrates via SystemVerilog
  - Ideally, supports remote debug over socket
  - Doesn't require recompile of anything but PSS for rerun
- Stub/mock environment support (Python?)
