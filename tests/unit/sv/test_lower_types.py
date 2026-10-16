"""Tests for PSS type lowering to SV IR nodes."""

import pytest
from zuspec.dataclasses import ir
from zuspec.be.sv.ir.sv_emit import SVEmitter

from pssc.driver import CompileError
from pssc.targets.sv.context import LoweringContext
from pssc.targets.sv.lower_types import (
    lower_enum,
    lower_struct,
    lower_resource,
    lower_buffer,
    lower_stream,
    lower_state,
)


@pytest.fixture
def ctx():
    return LoweringContext()


@pytest.fixture
def emitter():
    return SVEmitter()


class TestLowerEnum:
    def test_simple_enum(self, ctx, emitter):
        dt = ir.DataTypeEnum(name="cmd_e", items={"READ": 0, "WRITE": 1, "IDLE": 2})
        sv = lower_enum(ctx, dt)
        text = emitter.emit_one(sv)
        assert "typedef enum" in text
        assert "cmd_e" in text
        assert "READ" in text
        assert "WRITE" in text

    def test_enum_with_explicit_values(self, ctx, emitter):
        dt = ir.DataTypeEnum(name="status_e", items={"OK": 0, "ERR": 5, "TIMEOUT": 10})
        sv = lower_enum(ctx, dt)
        text = emitter.emit_one(sv)
        assert "OK" in text
        assert "ERR" in text
        assert "TIMEOUT" in text


class TestLowerStruct:
    def test_struct_no_rand(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="Packet",
            super=None,
            fields=[
                ir.Field(name="addr", datatype=ir.DataTypeInt(bits=32, signed=False)),
                ir.Field(name="data", datatype=ir.DataTypeInt(bits=64, signed=False)),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "class Packet;" in text
        assert "bit [31:0] addr;" in text
        assert "bit [63:0] data;" in text
        # No rand qualifier
        assert "rand " not in text

    def test_struct_with_rand(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="RandPacket",
            super=None,
            fields=[
                ir.Field(name="addr", datatype=ir.DataTypeInt(bits=32, signed=False),
                         rand_kind="rand"),
                ir.Field(name="data", datatype=ir.DataTypeInt(bits=64, signed=False),
                         rand_kind="rand"),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "rand bit [31:0] addr;" in text
        assert "rand bit [63:0] data;" in text

    def test_struct_with_constraint(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="ConstrainedPacket",
            super=None,
            fields=[
                ir.Field(name="addr", datatype=ir.DataTypeInt(bits=32, signed=False),
                         rand_kind="rand"),
            ],
            functions=[
                ir.Function(
                    name="addr_align",
                    body=[
            ir.StmtExpr(expr=ir.ExprCompare(
                            left=ir.ExprRefLocal(name="addr"),
                            ops=[ir.CmpOp.Eq],
                            comparators=[ir.ExprConstant(value=0)],
                        )),
                    ],
                    metadata={"_is_constraint": True},
                ),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "constraint addr_align" in text
        assert "addr" in text

    def test_struct_with_inheritance(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="ExtPacket",
            super=ir.DataTypeRef(ref_name="BasePacket"),
            fields=[
                ir.Field(name="extra", datatype=ir.DataTypeInt(bits=8, signed=False)),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "class ExtPacket extends BasePacket;" in text

    def test_struct_with_string_field(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="Named",
            super=None,
            fields=[
                ir.Field(name="label", datatype=ir.DataTypeString()),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "string label;" in text

    def test_struct_with_enum_field(self, ctx, emitter):
        enum_dt = ir.DataTypeEnum(name="mode_e", items={"A": 0, "B": 1})
        dt = ir.DataTypeStruct(
            name="WithEnum",
            super=None,
            fields=[
                ir.Field(name="mode", datatype=enum_dt),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "mode_e mode;" in text


class TestLowerFlowObjects:
    def test_resource(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="channel_s",
            super=None,
            fields=[
                ir.Field(name="id", datatype=ir.DataTypeInt(bits=8, signed=False)),
            ],
        )
        sv = lower_resource(ctx, dt)
        text = emitter.emit_one(sv)
        assert "extends zsp_resource" in text
        assert "bit [7:0] id;" in text

    def test_buffer(self, ctx, emitter):
        dt = ir.DataTypeStruct(name="buf_s", super=None, fields=[])
        sv = lower_buffer(ctx, dt)
        text = emitter.emit_one(sv)
        assert "extends zsp_buffer" in text

    def test_stream(self, ctx, emitter):
        dt = ir.DataTypeStruct(name="stream_s", super=None, fields=[])
        sv = lower_stream(ctx, dt)
        text = emitter.emit_one(sv)
        assert "extends zsp_stream" in text

    def test_state(self, ctx, emitter):
        dt = ir.DataTypeStruct(name="state_s", super=None, fields=[])
        sv = lower_state(ctx, dt)
        text = emitter.emit_one(sv)
        assert "extends zsp_state" in text


class TestCollectionTypes:
    # A collection field cannot be declared by this target: its type string
    # goes before the name, and SV puts a collection's dimension after it.
    # It used to emit `bit [31:0] [$] data;`, which is not SV, and these
    # tests asserted that text. Refused instead (ruling R9).
    @pytest.mark.parametrize("dtype, kind", [
        (ir.DataTypeList(element_type=ir.DataTypeInt(bits=32, signed=False)),
         "list"),
        (ir.DataTypeArray(element_type=ir.DataTypeInt(bits=8, signed=False),
                          size=16), "array"),
        (ir.DataTypeMap(key_type=ir.DataTypeString(),
                        value_type=ir.DataTypeInt(bits=32, signed=False)),
         "map"),
        (ir.DataTypeSet(element_type=ir.DataTypeInt(bits=8, signed=False)),
         "set"),
    ])
    def test_a_collection_field_is_refused(self, ctx, dtype, kind):
        dt = ir.DataTypeStruct(name="WithColl", super=None,
                               fields=[ir.Field(name="data", datatype=dtype)])
        with pytest.raises(CompileError, match=f"cannot declare a {kind}"):
            lower_struct(ctx, dt)

    def test_chandle_field(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="WithHandle",
            super=None,
            fields=[
                ir.Field(name="ptr", datatype=ir.DataTypeChandle()),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "chandle ptr;" in text

    def test_randc_field(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="WithRandc",
            super=None,
            fields=[
                ir.Field(name="id", datatype=ir.DataTypeInt(bits=4, signed=False),
                         rand_kind="randc"),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "randc bit [3:0] id;" in text

    def test_signed_int_field(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="WithSigned",
            super=None,
            fields=[
                ir.Field(name="offset", datatype=ir.DataTypeInt(bits=16, signed=True)),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "int signed [15:0] offset;" in text

    def test_single_bit_field(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="WithBit",
            super=None,
            fields=[
                ir.Field(name="valid", datatype=ir.DataTypeInt(bits=1, signed=False)),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "bit valid;" in text

    def test_int32_signed(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="WithInt",
            super=None,
            fields=[
                ir.Field(name="count", datatype=ir.DataTypeInt(bits=32, signed=True)),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "int count;" in text

    def test_ref_type_field(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="WithRef",
            super=None,
            fields=[
                ir.Field(name="other", datatype=ir.DataTypeRef(ref_name="other_struct")),
            ],
        )
        sv = lower_struct(ctx, dt)
        text = emitter.emit_one(sv)
        assert "other_struct other;" in text


class TestFlowObjectInheritance:
    def test_resource_with_explicit_super(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="my_res",
            super=ir.DataTypeRef(ref_name="base_resource"),
            fields=[],
        )
        sv = lower_resource(ctx, dt)
        text = emitter.emit_one(sv)
        # Explicit super takes precedence over default zsp_resource
        assert "extends base_resource" in text

    def test_buffer_with_fields(self, ctx, emitter):
        dt = ir.DataTypeStruct(
            name="data_buf",
            super=None,
            fields=[
                ir.Field(name="payload", datatype=ir.DataTypeInt(bits=64, signed=False)),
                ir.Field(name="valid", datatype=ir.DataTypeInt(bits=1, signed=False)),
            ],
        )
        sv = lower_buffer(ctx, dt)
        text = emitter.emit_one(sv)
        assert "extends zsp_buffer" in text
        assert "bit [63:0] payload;" in text
        assert "bit valid;" in text
