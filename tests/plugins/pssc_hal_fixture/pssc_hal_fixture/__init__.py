"""A stateless C API over an op-model component: the HAL fixture.

`op-model-c` generates a component OBJECT: every operation takes `_self`, and
the register bases live in it. This target generates the other shape a
driver layer often wants -- plain functions, each taking the register-window
bases it uses, with every register access spelled in the platform's own
macros. It is the worked example for that path (`custom-generator-styles.md`,
Level 2), and everything in it goes through the published surface:

  * `handle_param` -> None, and `signature` from `OpModel.unpacked_signature`;
  * `CBodyEmitter.model_call`, so a call passes the callee's bases;
  * the register hooks, for `DEV_READ32_INTO` / `DEV_FIELD_SET` / `DEV_WRITE32`;
  * `linkage`, `static` for an operation the model does not export;
  * the lifecycle pair, left out: there is no object to construct;
  * `is_extern_type` and `seam_includes` on its style;
  * `emit_extra_files`, for the register map the macros need and a JSON
    description of the API;
  * `pssc.annotations`, for what the model says about all of the above.
"""
from .backend import HalBackend, HalEmitter, HalFixtureTarget, HalStyle

__all__ = ["HalBackend", "HalEmitter", "HalFixtureTarget", "HalStyle"]
