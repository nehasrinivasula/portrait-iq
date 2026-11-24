"""
A read-only parser for Java's object serialization stream format (SCRUM-123).

RTI Monitor saves a session (File > Save) as a Java-serialized object stream,
the ".ser" the UMF RTI import mode ingests. Python cannot load Java classes, and
we would not want it to: this module only *parses* the documented stream format
(Java Object Serialization Specification, chapter 6 "Object Serialization Stream
Protocol") into plain Python values. No class is ever looked up, loaded or run,
so an uploaded file cannot execute anything -- the usual danger of Java
deserialization does not apply.

What comes out:
  JavaObject  class name, its field values per class in the hierarchy, and any
              extra data a class's own writeObject() appended ("annotations")
  JavaEnum    class name and constant name
  JavaArray   element type and values
  str / bytes / int / float / bool / None for strings, block data and primitives
  JavaClass   a serialized Class object (rare)

Limits keep a hostile or truncated file from exhausting memory or the stack:
MAX_HANDLES objects, MAX_DEPTH nesting, and every length is checked against the
bytes that remain. Any violation raises JavaSerError.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

STREAM_MAGIC = 0xACED
STREAM_VERSION = 5

TC_NULL = 0x70
TC_REFERENCE = 0x71
TC_CLASSDESC = 0x72
TC_OBJECT = 0x73
TC_STRING = 0x74
TC_ARRAY = 0x75
TC_CLASS = 0x76
TC_BLOCKDATA = 0x77
TC_ENDBLOCKDATA = 0x78
TC_RESET = 0x79
TC_BLOCKDATALONG = 0x7A
TC_EXCEPTION = 0x7B
TC_LONGSTRING = 0x7C
TC_PROXYCLASSDESC = 0x7D
TC_ENUM = 0x7E
BASE_HANDLE = 0x7E0000

SC_WRITE_METHOD = 0x01
SC_SERIALIZABLE = 0x02
SC_EXTERNALIZABLE = 0x04
SC_BLOCK_DATA = 0x08
SC_ENUM = 0x10

MAX_HANDLES = 5_000_000
MAX_DEPTH = 400

_PRIMITIVES = {
    "B": (">b", 1), "C": (">H", 2), "D": (">d", 8), "F": (">f", 4),
    "I": (">i", 4), "J": (">q", 8), "S": (">h", 2), "Z": (">?", 1),
}


class JavaSerError(ValueError):
    """The bytes are not a well-formed Java serialization stream we can read."""


@dataclass(eq=False)
class ClassDesc:
    name: str
    flags: int = 0
    fields: list[tuple[str, str]] = field(default_factory=list)   # (type code, name)
    super: "ClassDesc | None" = None
    proxy_interfaces: list[str] = field(default_factory=list)

    def hierarchy(self) -> list["ClassDesc"]:
        """This class and its serializable superclasses, superclass first."""
        out, c = [], self
        while c is not None:
            out.append(c)
            c = c.super
        return out[::-1]


@dataclass(eq=False)
class JavaObject:
    class_name: str
    fields: dict[str, object] = field(default_factory=dict)          # all classes' fields, merged
    annotations: dict[str, list] = field(default_factory=dict)       # class name -> writeObject extras

    @property
    def simple_name(self) -> str:
        return self.class_name.rsplit(".", 1)[-1]


@dataclass(eq=False)
class JavaEnum:
    class_name: str
    constant: str


@dataclass(eq=False)
class JavaArray:
    class_name: str
    values: list


@dataclass(eq=False)
class JavaClass:
    class_name: str


# Stands in for an object whose class the caller asked to skip: it is parsed
# (so the stream stays in step and back-references still resolve) but its
# contents are not kept.
SKIPPED = JavaObject("<skipped>")


class _Reader:
    def __init__(self, data: bytes, skip_prefixes: tuple[str, ...] = ()):
        self.data = data
        self.pos = 0
        self.handles: list[object] = []
        self.depth = 0
        self.skip_prefixes = skip_prefixes
        self.skipping = 0          # > 0 while inside a skipped object

    # -- primitives ---------------------------------------------------------
    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise JavaSerError(f"stream ends early (need {n} bytes at offset {self.pos})")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def unpack(self, fmt: str, n: int):
        return struct.unpack(fmt, self.take(n))[0]

    def utf(self, long: bool = False) -> str:
        n = self.unpack(">q", 8) if long else self.unpack(">H", 2)
        if self.skipping:
            self.take(n)
            return ""
        # Java's "modified UTF-8": decoding with surrogatepass and errors
        # replaced is close enough for names; we never need byte-exact text.
        return self.take(n).decode("utf-8", errors="replace")

    def peek(self) -> int:
        if self.pos >= len(self.data):
            raise JavaSerError("stream ends early")
        return self.data[self.pos]

    def new_handle(self, obj) -> int:
        if len(self.handles) >= MAX_HANDLES:
            raise JavaSerError("too many objects in the stream")
        self.handles.append(obj)
        return len(self.handles) - 1

    def ref(self):
        h = self.unpack(">i", 4) - BASE_HANDLE
        if not 0 <= h < len(self.handles):
            raise JavaSerError(f"bad back-reference {h}")
        return self.handles[h]

    # -- grammar ------------------------------------------------------------
    def content(self, allow_block: bool = True):
        """One stream element: an object or, where allowed, a block of raw data."""
        tc = self.peek()
        if tc == TC_BLOCKDATA:
            if not allow_block:
                raise JavaSerError("unexpected block data")
            self.pos += 1
            return self.take(self.u8())
        if tc == TC_BLOCKDATALONG:
            if not allow_block:
                raise JavaSerError("unexpected block data")
            self.pos += 1
            return self.take(self.unpack(">i", 4))
        return self.object()

    def object(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise JavaSerError("objects nested too deeply")
        try:
            tc = self.u8()
            if tc == TC_NULL:
                return None
            if tc == TC_REFERENCE:
                return self.ref()
            if tc == TC_STRING:
                s = self.utf()
                self.new_handle(s)
                return s
            if tc == TC_LONGSTRING:
                s = self.utf(long=True)
                self.new_handle(s)
                return s
            if tc in (TC_CLASSDESC, TC_PROXYCLASSDESC):
                self.pos -= 1
                return self.class_desc()
            if tc == TC_OBJECT:
                return self.new_object()
            if tc == TC_ARRAY:
                return self.array()
            if tc == TC_ENUM:
                desc = self.class_desc()
                e = JavaEnum(desc.name if desc else "?", "")
                self.new_handle(e)
                e.constant = self.object()
                return e
            if tc == TC_CLASS:
                desc = self.class_desc()
                c = JavaClass(desc.name if desc else "?")
                self.new_handle(c)
                return c
            if tc == TC_RESET:
                self.handles.clear()
                return self.object()
            if tc == TC_EXCEPTION:
                raise JavaSerError("the stream records a write failure (TC_EXCEPTION)")
            raise JavaSerError(f"unknown type code 0x{tc:02x} at offset {self.pos - 1}")
        finally:
            self.depth -= 1

    def class_desc(self) -> ClassDesc | None:
        tc = self.u8()
        if tc == TC_NULL:
            return None
        if tc == TC_REFERENCE:
            d = self.ref()
            if not isinstance(d, ClassDesc):
                raise JavaSerError("back-reference is not a class description")
            return d
        if tc == TC_PROXYCLASSDESC:
            d = ClassDesc("<proxy>")
            self.new_handle(d)
            count = self.unpack(">i", 4)
            if count < 0 or count > 65535:
                raise JavaSerError("bad proxy interface count")
            d.proxy_interfaces = [self.utf() for _ in range(count)]
            self.skip_annotations()
            d.super = self.class_desc()
            d.flags = SC_SERIALIZABLE
            return d
        if tc != TC_CLASSDESC:
            raise JavaSerError(f"expected a class description, got 0x{tc:02x}")
        name = self.utf()
        self.take(8)                     # serialVersionUID
        d = ClassDesc(name)
        self.new_handle(d)
        d.flags = self.u8()
        count = self.unpack(">h", 2)
        if count < 0:
            raise JavaSerError("bad field count")
        for _ in range(count):
            code = chr(self.u8())
            fname = self.utf()
            if code in "L[":
                self.object()            # the field's type name, as a string
            elif code not in _PRIMITIVES:
                raise JavaSerError(f"bad field type {code!r}")
            d.fields.append((code, fname))
        self.skip_annotations()
        d.super = self.class_desc()
        return d

    def skip_annotations(self) -> list:
        """Class/object annotations: contents up to TC_ENDBLOCKDATA."""
        out = []
        while True:
            if self.peek() == TC_ENDBLOCKDATA:
                self.pos += 1
                return out
            out.append(self.content())

    def new_object(self) -> JavaObject:
        desc = self.class_desc()
        if desc is None:
            raise JavaSerError("object without a class description")
        skip = bool(self.skip_prefixes) and desc.name.startswith(self.skip_prefixes)
        if skip or self.skipping:
            self.skipping += 1
            try:
                self.new_handle(SKIPPED)
                self._read_object_data(desc, JavaObject(desc.name))
            finally:
                self.skipping -= 1
            return SKIPPED
        obj = JavaObject(desc.name)
        self.new_handle(obj)
        self._read_object_data(desc, obj)
        return obj

    def _read_object_data(self, desc: ClassDesc, obj: JavaObject) -> None:
        if desc.flags & SC_EXTERNALIZABLE:
            # writeExternal() writes the whole object as one block of external
            # data; no superclass fields are written, even for serializable
            # superclasses (e.g. RTI's ByteSeq over AbstractSequence).
            if not desc.flags & SC_BLOCK_DATA:
                raise JavaSerError(f"{desc.name}: old-format externalizable data is not readable")
            obj.annotations[desc.name] = self.skip_annotations()
            return
        for c in desc.hierarchy():
            for code, fname in c.fields:
                obj.fields[fname] = self.value(code)
            if c.flags & SC_WRITE_METHOD:
                obj.annotations[c.name] = self.skip_annotations()

    def array(self) -> JavaArray:
        desc = self.class_desc()
        if desc is None:
            raise JavaSerError("array without a class description")
        arr = JavaArray(desc.name, [])
        self.new_handle(arr)
        n = self.unpack(">i", 4)
        code = desc.name[1:2] if desc.name.startswith("[") else "L"
        if n < 0:
            raise JavaSerError("bad array length")
        if code in _PRIMITIVES:
            fmt, size = _PRIMITIVES[code]
            if n * size > len(self.data) - self.pos:
                raise JavaSerError("array longer than the stream")
            if self.skipping:
                self.take(n * size)
            elif code == "B":
                arr.values = list(self.take(n))   # keep bytes cheap
            else:
                arr.values = [self.unpack(fmt, size) for _ in range(n)]
        else:
            if n > len(self.data) - self.pos:     # every element takes at least one byte
                raise JavaSerError("array longer than the stream")
            arr.values = [self.object() for _ in range(n)]
        return arr

    def value(self, code: str):
        if code in _PRIMITIVES:
            fmt, size = _PRIMITIVES[code]
            return self.unpack(fmt, size)
        return self.object()


def read_stream(data: bytes, skip_prefixes: tuple[str, ...] = ()) -> list:
    """Every top-level element of a serialization stream, in order: objects and
    blocks of raw data (bytes). Objects whose class name starts with one of
    skip_prefixes -- and everything inside them -- are parsed but not kept
    (returned as SKIPPED), to bound memory on large files. Raises JavaSerError
    if the stream is malformed."""
    r = _Reader(data, skip_prefixes)
    if len(data) < 4:
        raise JavaSerError("too short to be a Java serialization stream")
    magic, version = struct.unpack(">HH", r.take(4))
    if magic != STREAM_MAGIC or version != STREAM_VERSION:
        raise JavaSerError("not a Java serialization stream (bad magic or version)")
    out = []
    while r.pos < len(data):
        out.append(r.content())
    return out
