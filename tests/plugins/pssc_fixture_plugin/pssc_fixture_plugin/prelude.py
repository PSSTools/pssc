"""A `pssc.preludes` contributor: the fixture's annotation package, in scope
for every target, so a model annotated for the fixture also compiles on
`op-model-c` and `op-model-sv` without passing the package by hand."""
from importlib import resources


def contribute(target_name, opts):
    text = (resources.files(__package__) / "fixture_ann_pkg.pss").read_text()
    return [("fixture_ann_pkg.pss", text)]
