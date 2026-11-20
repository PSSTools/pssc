"""Field kinds from a register description: ``--reg-semantics FILE`` (P5.6).

PSS gives a register one access (READWRITE / READONLY / WRITEONLY); a
register description says more per field: read-clear, write-1-to-clear,
write-1-to-set, written by the hardware. :func:`load` reads such a file into
a table of the fake's field kinds (``bc_session.FIELD_KINDS``), and
:func:`kinds_for` picks the entry for one register instance. A
``@pssunit_pkg::field_access`` annotation still wins over the file.

Formats, by suffix:

- ``.xml``: IP-XACT (1685-2009 ``spirit``, 2014 and 2022 ``ipxact``; the
  namespace is ignored). A field's ``access``, ``readAction``,
  ``modifiedWriteValue`` and ``volatile`` give its kind.
- ``.rdl``: SystemRDL, read with ``systemrdl-compiler`` (the ``pssc[rdl]``
  extra). ``onread = rclr``, ``onwrite = woclr / woset``, ``sw`` and the
  hardware's writes (``hw = w``, ``hwclr``, ``hwset``, ``counter``) give the
  kind. A file with no ``addrmap`` (a bank of ``regfile`` or ``reg`` types) is
  read as if each root-scope ``regfile`` (else ``reg``) type were one block.
- ``.json``: the table itself, ``{"<reg>": {"<field>": "<kind>"}}``.

The table's keys are register paths from the address block (or address map),
register files included and array indices dropped: ``csr`` or ``ch.csr``.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Iterator, Optional
from xml.etree import ElementTree

#: register path -> field name -> kind
Table = Dict[str, Dict[str, str]]


def load(path: str) -> Table:
    """The field-kind table of register description ``path``."""
    if path.endswith(".json"):
        with open(path) as fp:
            table = json.load(fp)
        return {reg: {f: str(k).upper() for f, k in fields.items()}
                for reg, fields in table.items()}
    if path.endswith(".xml"):
        return ipxact(ElementTree.parse(path).getroot())
    if path.endswith(".rdl"):
        return rdl(path)
    raise ValueError(f"--reg-semantics {path}: expected an IP-XACT .xml, "
                     "a SystemRDL .rdl or a .json table")


def kind(access: str, read_action: str = "", write_value: str = "",
         volatile: bool = False) -> str:
    """The fake's kind for a field described in IP-XACT terms: ``access``
    (``read-write``, ``read-only``, ``write-only``, ``read-writeOnce``,
    ``writeOnce``), ``read_action`` (``clear``, ``set``, ``modify``) and
    ``write_value`` (``oneToClear``, ``oneToSet``, ...)."""
    if read_action == "clear":
        return "RC"
    if write_value == "oneToClear":
        return "W1C"
    if write_value == "oneToSet":
        return "W1S"
    if access == "read-only":
        return "RO"
    if access in ("write-only", "writeOnce"):
        return "WO"
    return "VOLATILE" if volatile else "RW"


def ipxact(root) -> Table:
    """The field-kind table of IP-XACT component ``root``."""
    table: Table = {}
    for block in _all(root, "addressBlock"):
        for path, reg in _registers(block, ""):
            fields = table.setdefault(path, {})
            for f in _children(reg, "field"):
                fields[_text(f, "name")] = kind(
                    _deep(f, "access") or _text(reg, "access") or "read-write",
                    _deep(f, "readAction"), _deep(f, "modifiedWriteValue"),
                    _deep(f, "volatile") == "true")
    return table


def rdl(path: str) -> Table:
    """The field-kind table of SystemRDL file ``path``: register paths
    from the last ``addrmap``, or from each root-scope ``regfile`` (else
    ``reg``) type when the file has no ``addrmap``."""
    try:
        from systemrdl import RDLCompiler, RDLCompileError
        from systemrdl import component
        from systemrdl.node import RegNode
    except ImportError:
        raise ValueError(f"--reg-semantics {path}: reading SystemRDL needs "
                         "systemrdl-compiler: pip install pssc[rdl]") from None
    import os
    import tempfile
    rdlc = RDLCompiler()
    wrapper = None
    try:
        rdlc.compile_file(path)
        roots = {name: comp for name, (comp, _) in
                 rdlc.namespace.type_ns_stack[0].items()}
        top_name = None
        if not any(isinstance(c, component.Addrmap) for c in roots.values()):
            # No address map: instantiate each block type in one, and key
            # the registers from the block (its own name dropped).
            blocks = [n for n, c in roots.items()
                      if isinstance(c, component.Regfile)] or \
                [n for n, c in roots.items() if isinstance(c, component.Reg)]
            top_name = "pssc_reg_semantics_top"
            fd, wrapper = tempfile.mkstemp(suffix=".rdl")
            with os.fdopen(fd, "w") as fp:
                fp.write("addrmap %s {\n%s};\n" % (top_name, "".join(
                    f"    {n} {n};\n" for n in blocks)))
            rdlc.compile_file(wrapper)
        top = rdlc.elaborate(top_name).top
    except RDLCompileError as e:
        raise ValueError(f"--reg-semantics {path}: {e}") from None
    finally:
        if wrapper is not None:
            os.unlink(wrapper)
    table: Table = {}
    for reg in top.descendants(unroll=False):
        if not isinstance(reg, RegNode):
            continue
        parts = reg.get_path(empty_array_suffix="").split(".")[1:]
        if top_name is not None and len(parts) > 1:
            parts = parts[1:]
        fields = table.setdefault(".".join(parts), {})
        for f in reg.fields():
            fields[f.inst_name] = kind(*_rdl_terms(f))
    return table


def _rdl_terms(f):
    """(access, read_action, write_value, volatile) of RDL field ``f``."""
    sw = f.get_property("sw").name
    onread = f.get_property("onread")
    onwrite = f.get_property("onwrite")
    access = {"r": "read-only", "w": "write-only",
              "w1": "writeOnce"}.get(sw, "read-write")
    read_action = "clear" if onread is not None and \
        onread.name == "rclr" else ""
    write_value = {"woclr": "oneToClear", "woset": "oneToSet"}.get(
        onwrite.name if onwrite is not None else "", "")
    volatile = bool(f.is_hw_writable or f.get_property("hwclr")
                    or f.get_property("hwset") or f.get_property("counter"))
    return access, read_action, write_value, volatile


def kinds_for(table: Table, path: str) -> Dict[str, str]:
    """The entry of ``table`` for register instance ``path``
    (``top.regs.ch[1].csr``): the longest key that ends the path, indices
    dropped, on a ``.`` boundary. Empty when none does."""
    plain = re.sub(r"\[\d+\]", "", path)
    best: Optional[str] = None
    for key in table:
        if (plain == key or plain.endswith("." + key)) and (
                best is None or len(key) > len(best)):
            best = key
    return dict(table[best]) if best is not None else {}


def _registers(node, prefix: str) -> Iterator:
    for reg in _children(node, "register"):
        yield prefix + _text(reg, "name"), reg
    for rf in _children(node, "registerFile"):
        yield from _registers(rf, prefix + _text(rf, "name") + ".")


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _children(node, name: str):
    return [c for c in node if _local(c.tag) == name]


def _all(node, name: str):
    return [c for c in node.iter() if _local(c.tag) == name]


def _text(node, name: str) -> str:
    c = _children(node, name)
    return (c[0].text or "").strip() if c else ""


def _deep(node, name: str) -> str:
    """The first ``name`` under ``node`` at any depth (2022 puts a field's
    access under ``fieldAccessPolicies``)."""
    c = _all(node, name)
    return (c[0].text or "").strip() if c else ""
