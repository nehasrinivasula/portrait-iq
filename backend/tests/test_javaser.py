"""The read-only Java serialization parser behind the RTI Monitor .ser import
(SCRUM-123). Streams are built here with a tiny writer that follows the Java
Object Serialization Stream Protocol, so no Java is needed to run the tests."""
import struct

import pytest

from app.collectors import javaser
from app.collectors.javaser import JavaArray, JavaEnum, JavaObject, JavaSerError, read_stream

SC_WRITE_METHOD, SC_SERIALIZABLE, SC_EXTERNALIZABLE, SC_BLOCK_DATA, SC_ENUM = 1, 2, 4, 8, 16


class W:
    """Minimal Java serialization writer: enough to build test streams."""

    def __init__(self):
        self.b = bytearray(b"\xac\xed\x00\x05")
        self.next_handle = 0x7E0000
        self.handles = {}

    def _utf(self, s):
        d = s.encode()
        self.b += struct.pack(">H", len(d)) + d

    def _handle(self, key):
        self.handles[key] = self.next_handle
        self.next_handle += 1

    def ref(self, key):
        self.b += b"\x71" + struct.pack(">i", self.handles[key])

    def null(self):
        self.b += b"\x70"

    def string(self, s, key=None):
        self.b += b"\x74"
        self._utf(s)
        self._handle(key or ("str", s))

    def block(self, data: bytes):
        self.b += b"\x77" + bytes([len(data)]) + data

    def end(self):
        self.b += b"\x78"

    def classdesc(self, name, flags, fields=(), super_=None):
        """fields: (type code, name, type signature for objects)."""
        if ("desc", name) in self.handles:
            self.ref(("desc", name))
            return
        self.b += b"\x72"
        self._utf(name)
        self.b += b"\x00" * 8 + bytes([flags]) + struct.pack(">h", len(fields))
        self._handle(("desc", name))
        for code, fname, sig in fields:
            self.b += code.encode()
            self._utf(fname)
            if code in "L[":
                self.string(sig)
        self.end()                       # class annotations
        if super_:
            self.classdesc(*super_)
        else:
            self.null()

    def obj(self, name, flags, fields=(), super_=None, key=None):
        self.b += b"\x73"
        self.classdesc(name, flags, fields, super_)
        self._handle(key or ("obj", name, len(self.b)))

    def enum(self, cls, const):
        self.b += b"\x7e"
        self.classdesc(cls, SC_SERIALIZABLE | SC_ENUM, (), ("java.lang.Enum", SC_SERIALIZABLE | SC_ENUM))
        self._handle(("enum", cls, const, len(self.b)))
        self.string(const)

    def data(self):
        return bytes(self.b)


def test_serializable_object_fields_and_back_references():
    w = W()
    point = ("com.example.Point", SC_SERIALIZABLE, (("I", "x", None), ("L", "label", "Ljava/lang/String;")))
    w.obj(*point, key="p1")
    w.b += struct.pack(">i", 7)
    w.string("hello", key="s")
    w.obj("com.example.Point", SC_SERIALIZABLE)   # class desc comes back as a reference
    w.b += struct.pack(">i", 9)
    w.ref("s")                                     # same string, by reference
    w.ref("p1")                                    # the first object again
    a, b, again = read_stream(w.data())
    assert isinstance(a, JavaObject) and a.simple_name == "Point"
    assert a.fields == {"x": 7, "label": "hello"} and b.fields == {"x": 9, "label": "hello"}
    assert again is a


def test_write_method_extras_and_superclass_fields_in_order():
    w = W()
    w.obj("com.example.Child", SC_SERIALIZABLE | SC_WRITE_METHOD, (("J", "c", None),),
          super_=("com.example.Base", SC_SERIALIZABLE, (("Z", "b", None),)))
    w.b += b"\x01"                         # Base.b (superclass first)
    w.b += struct.pack(">q", 5)            # Child.c
    w.block(b"\x00\x00\x00\x02")           # what Child.writeObject() appended
    w.string("extra")
    w.end()
    (o,) = read_stream(w.data())
    assert o.fields == {"b": True, "c": 5}
    assert o.annotations["com.example.Child"] == [b"\x00\x00\x00\x02", "extra"]


def test_externalizable_writes_one_block_and_no_superclass_fields():
    # RTI's ByteSeq is Externalizable over a Serializable AbstractSequence with a
    # field: Java writes only writeExternal()'s data, never that field.
    w = W()
    w.obj("com.rti.dds.infrastructure.ByteSeq", SC_EXTERNALIZABLE | SC_BLOCK_DATA,
          super_=("com.rti.dds.util.AbstractSequence", SC_SERIALIZABLE,
                  (("L", "_elementType", "Ljava/lang/Class;"),)))
    w.block(b"\x00\x00\x00\x00")
    w.end()
    w.string("after")
    seq, after = read_stream(w.data())
    assert seq.fields == {} and seq.annotations == {"com.rti.dds.infrastructure.ByteSeq": [b"\x00\x00\x00\x00"]}
    assert after == "after"


def test_arrays_and_enums():
    w = W()
    w.b += b"\x75"
    w.classdesc("[I", SC_SERIALIZABLE)
    w._handle(("arr", 1))
    w.b += struct.pack(">i", 3) + struct.pack(">iii", 1, 2, 3)
    w.enum("com.example.Color", "RED")
    arr, color = read_stream(w.data())
    assert isinstance(arr, JavaArray) and arr.values == [1, 2, 3]
    assert isinstance(color, JavaEnum) and color.constant == "RED"


def test_skip_prefixes_parse_but_do_not_keep():
    w = W()
    w.obj("com.rti.dds.typecode.TypeCode", SC_SERIALIZABLE, (("L", "name", "Ljava/lang/String;"),))
    w.string("big type description")
    w.string("kept")
    skipped, kept = read_stream(w.data(), skip_prefixes=("com.rti.dds.typecode.",))
    assert skipped is javaser.SKIPPED
    assert kept == "kept"                  # the stream stayed in step


@pytest.mark.parametrize("data, match", [
    (b"PK\x03\x04", "not a Java serialization stream"),
    (b"\xac\xed", "too short"),
    (b"\xac\xed\x00\x05\x74\x00\x10abc", "ends early"),          # truncated string
    (b"\xac\xed\x00\x05\x71\x00\x7e\x00\x05", "back-reference"),   # reference to nothing
    (b"\xac\xed\x00\x05\x42", "unknown type code"),
    (b"\xac\xed\x00\x05\x7b", "TC_EXCEPTION"),
])
def test_malformed_streams_are_refused(data, match):
    with pytest.raises(JavaSerError, match=match):
        read_stream(data)


def test_deep_nesting_is_refused(monkeypatch):
    monkeypatch.setattr(javaser, "MAX_DEPTH", 20)
    w = W()
    node = ("com.example.Node", SC_SERIALIZABLE, (("L", "next", "Lcom/example/Node;"),))
    for _ in range(30):
        w.obj(*node)
    w.null()
    with pytest.raises(JavaSerError, match="nested too deeply"):
        read_stream(w.data())


def test_huge_declared_array_is_refused():
    w = W()
    w.b += b"\x75"
    w.classdesc("[Ljava.lang.Object;", SC_SERIALIZABLE)
    w._handle(("arr", 2))
    w.b += struct.pack(">i", 2_000_000_000)
    with pytest.raises(JavaSerError, match="longer than the stream"):
        read_stream(w.data())
