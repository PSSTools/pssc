"""The debug front end (plan P2.2): what a source-level debugger knows that
the engine's debug stub (be-bc ``interp.debug.Stub``) does not -- lines,
names, expressions. The stub speaks coroutines, pcs and addresses; this
package turns a user's ``file:line``, register path or PSS expression into
those, and the stub's answers back into source terms.
"""
