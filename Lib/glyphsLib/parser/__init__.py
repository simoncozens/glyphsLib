# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from collections import OrderedDict
from io import open
import re
import logging
import sys

from glyphsLib.util import tostr
import glyphsLib

logger = logging.getLogger(__name__)


class Parser:
    """Parses Python dictionaries from Glyphs source files."""

    # FIXME: Why was value_re overwritten? Renamed first one to value_re_shared.
    value_re_shared = r'(".*?(?<!\\)"|[-_./$A-Za-z0-9]+)'
    start_dict_re = re.compile(r"\s*{")
    end_dict_re = re.compile(r"\s*}")
    dict_delim_re = re.compile(r"\s*;")
    start_list_re = re.compile(r"\s*\(")
    end_list_re = re.compile(r"\s*\)")
    list_delim_re = re.compile(r"\s*,")
    attr_re = re.compile(r"\s*%s\s*=" % value_re_shared, re.DOTALL)
    value_re = re.compile(r"\s*%s" % value_re_shared, re.DOTALL)
    hex_re = re.compile(r"\s*<([A-Fa-f0-9]+)>", re.DOTALL)
    bytes_re = re.compile(r"\s*<([A-Za-z0-9+/=]+)>", re.DOTALL)

    def __init__(self, current_type=None, formatVersion=2):
        self.current_type = current_type
        self.formatVersion = formatVersion

    def parse(self, text):
        """Do the parsing."""

        text = tostr(text, encoding="utf-8")
        result, i = self._parse(text, 0)
        if text[i:].strip():
            self._fail("Unexpected trailing content", text, i)
        if self.current_type:
            if isinstance(result, list):
                return [ self.current_type.from_dict(x, formatVersion=self.formatVersion) for x in result ]
            return self.current_type.from_dict(result, formatVersion=self.formatVersion)
        return result

    def parse_into_object(self, res, text, formatVersion=2):
        """Parse data into an existing GSFont instance."""

        text = tostr(text, encoding="utf-8")
        result, i = self._parse(text, 0)
        res.__class__.from_dict(result, formatVersion=self.formatVersion, target=res)
        return i

    def _guess_current_type(self, parsed, value):
        if value.lower() in ("infinity", "inf", "nan"):
            # Those values would be accepted by `float()`
            # But `infinity` is a glyph name
            return str
        if parsed[-1] != '"':
            try:
                v = float(value)

                def current_type(_):
                    if v.is_integer():
                        return int(v)
                    return v

            except ValueError:
                current_type = str
        else:
            current_type = str
        return current_type

    def _parse(self, text, i):
        """Recursive function to parse a single dictionary, list, or value."""

        m = self.start_dict_re.match(text, i)
        if m:
            parsed = m.group(0)
            i += len(parsed)
            return self._parse_dict(text, i)

        m = self.start_list_re.match(text, i)
        if m:
            parsed = m.group(0)
            i += len(parsed)
            return self._parse_list(text, i)

        m = self.value_re.match(text, i)
        if m:
            parsed = m.group(0)
            i += len(parsed)
            value = self._trim_value(m.group(1))

            return value, i

        m = self.hex_re.match(text, i)
        if m:
            from glyphsLib.types import BinaryData

            parsed, value = m.group(0), m.group(1)
            decoded = BinaryData.fromHex(value)
            i += len(parsed)
            return decoded, i
        else:
            self._fail("Unexpected content", text, i)

    def _parse_dict(self, text, i):
        """Parse a dictionary from source text starting at i."""
        end_match = self.end_dict_re.match(text, i)
        python_dict = OrderedDict()
        while not end_match:
            m = self.attr_re.match(text, i)
            if not m:
                self._fail("Unexpected dictionary content", text, i)
            parsed, name = m.group(0), self._trim_value(m.group(1))
            i += len(parsed)

            result = self._parse(text, i)

            python_dict[name], i = result

            m = self.dict_delim_re.match(text, i)
            if not m:
                self._fail("Missing delimiter in dictionary before content", text, i)
            parsed = m.group(0)
            i += len(parsed)

            end_match = self.end_dict_re.match(text, i)
        parsed = end_match.group(0)
        i += len(parsed)
        return python_dict, i

    def _parse_list(self, text, i):
        """Parse a list from source text starting at i."""

        res = []
        end_match = self.end_list_re.match(text, i)
        while not end_match:
            list_item, i = self._parse(text, i)
            res.append(list_item)

            end_match = self.end_list_re.match(text, i)

            if not end_match:
                m = self.list_delim_re.match(text, i)
                if not m:
                    self._fail("Missing delimiter in list before content", text, i)
                parsed = m.group(0)
                i += len(parsed)

        parsed = end_match.group(0)
        i += len(parsed)
        return res, i

    # glyphs only supports octal escapes between \000 and \077 and hexadecimal
    # escapes between \U0000 and \UFFFF
    _unescape_re = re.compile(r"\\(?:(0[0-7]{2})|(?:U([0-9a-fA-F]{4})))")

    @staticmethod
    def _unescape_fn(m):
        if m.group(1):
            return chr(int(m.group(1), 8))
        return chr(int(m.group(2), 16))

    def _trim_value(self, value):
        """Trim double quotes off the ends of a value, un-escaping inner
        double quotes and literal backslashes. Also convert escapes to unicode.
        If the string is not quoted, return it unmodified.
        """

        if value[0] == '"':
            assert value[-1] == '"'
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return Parser._unescape_re.sub(Parser._unescape_fn, value)
        return value

    def _fail(self, message, text, i):
        """Raise an exception with given message and text at i."""

        raise ValueError("{}:\n{}".format(message, text[i : i + 79]))


def load(fp):
    """Read a .glyphs file. 'fp' should be (readable) file object.
    Return a GSFont object.
    """
    return loads(fp.read())


def loads(s):
    """Read a .glyphs file from a (unicode) str object, or from
    a UTF-8 encoded bytes object.
    Return a GSFont object.
    """
    p = Parser(current_type=glyphsLib.classes.GSFont)
    logger.info("Parsing .glyphs file")
    data = p.parse(s)
    return data


def main(args=None):
    """Roundtrip the .glyphs file given as an argument."""
    for arg in args:
        glyphsLib.dump(load(open(arg, "r", encoding="utf-8")), sys.stdout)


if __name__ == "__main__":
    main(sys.argv[1:])
