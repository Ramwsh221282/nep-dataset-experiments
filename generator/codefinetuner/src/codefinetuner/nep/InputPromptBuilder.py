"""Serialize already-processed input blocks; never extract or rewrite source."""
from difflib import unified_diff


class InputPromptBuilder:
    SCHEMA_VERSION = "nep-input-v1"

    def build(self, blocks: dict) -> str:
        from .protocol import SUFFIX, PREFIX, MIDDLE, SEP, CURSOR, check_path, plain, marker, parse_input

        if blocks.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError("Unsupported input block schema")
        target = blocks["edit_target"]
        path, text = target["path"], target["code_text"]
        check_path(path)
        plain(text)
        if target.get("position_encoding") != "utf8_bytes":
            raise ValueError("edit_target positions must use utf8_bytes")
        source = text.encode("utf-8")
        markers = target["markers"]
        if len(markers) < 2 or [m["id"] for m in markers] != list(range(1, len(markers) + 1)):
            raise ValueError("Markers must be consecutive starting at 1")
        offsets = [m["offset"] for m in markers]
        cursor = target["cursor"]["offset"]
        for offset in [*offsets, cursor]:
            if type(offset) is not int or not 0 <= offset <= len(source):
                raise ValueError("Offset outside edit_target")
            try:
                source[:offset].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Offset splits a UTF-8 character") from exc
        if offsets != sorted(offsets) or not offsets[0] <= cursor <= offsets[-1]:
            raise ValueError("Cursor or marker ordering is invalid")
        # A cursor at a shared boundary belongs to the preceding region. At the
        # very first boundary (including an empty region), put it after marker_1.
        parts = [source[:offsets[0]].decode("utf-8")]
        written = False
        for index, (start, end) in enumerate(zip(offsets, offsets[1:]), 1):
            parts.append(marker(index))
            if not written and start <= cursor <= end:
                parts.extend([source[start:cursor].decode("utf-8"), CURSOR, source[cursor:end].decode("utf-8")])
                written = True
            else:
                parts.append(source[start:end].decode("utf-8"))
        parts.append(marker(len(offsets)))
        context, seen = [], set()
        for group in ("retrieval_context", "current_file_context"):
            for fragment in blocks[group]:
                fragment_path, code = fragment["path"], fragment["code_text"]
                check_path(fragment_path)
                plain(code)
                if (group == "current_file_context") != (fragment_path == path):
                    raise ValueError("Context fragment is in the wrong context group")
                key = (fragment_path, code)
                if key in seen:
                    continue
                seen.add(key)
                context.append(SEP + fragment_path + "\n" + code + ("" if code.endswith("\n") else "\n"))
        history, latest = [], {}
        if not blocks["edit_history"]:
            raise ValueError("Input requires actual edit history")
        for index, step in enumerate(blocks["edit_history"], 1):
            if step["id"] != index:
                raise ValueError("History IDs must be chronological and consecutive")
            check_path(step["path"])
            before, after = step["before"], step["after"]
            plain(before)
            plain(after)
            if before == after or (step["path"] in latest and latest[step["path"]] != before):
                raise ValueError("History contains a stale or empty edit")
            latest[step["path"]] = after
            for line in unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                     fromfile="a/" + step["path"], tofile="b/" + step["path"], n=3):
                history.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        prompt = (SUFFIX + source[offsets[-1]:].decode("utf-8") + PREFIX + "".join(context)
                  + SEP + "edit_history\n" + "".join(history) + SEP + path + "\n" + "".join(parts) + MIDDLE)
        if parse_input(prompt).source != text:
            raise ValueError("Input blocks did not preserve edit_target")
        return prompt
