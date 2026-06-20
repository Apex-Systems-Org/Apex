import re
import shlex


class FlagParser:
    """Parse flags from command input strings.

    Usage:
        parser = FlagParser()
        parser.add_flag("silent", short="s", type=bool)
        parser.add_flag("reason", short="r", type=str)
        parser.add_flag("delete", short="d", type=str)

        flags, remaining = parser.parse("@user --reason raiding --silent")
        # flags = {"silent": True, "reason": "raiding", "delete": None}
        # remaining = "@user"
    """

    def __init__(self):
        self.flags = {}

    def add_flag(self, name: str, short: str = None, type: type = bool, default=None):
        self.flags[name] = {
            "short": short,
            "type": type,
            "default": default,
        }

    def parse(self, text: str) -> tuple[dict, str]:
        """Parse flags from text. Returns (flags_dict, remaining_text)."""
        if not text:
            return {name: f["default"] for name, f in self.flags.items()}, ""

        result = {name: f["default"] for name, f in self.flags.items()}
        remaining_parts = []

        # Build lookup maps
        long_map = {}  # --name -> flag_name
        short_map = {}  # -s -> flag_name
        for name, f in self.flags.items():
            long_map[f"--{name}"] = name
            if f["short"]:
                short_map[f"-{f['short']}"] = name

        # Split respecting quotes
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()

        i = 0
        while i < len(parts):
            part = parts[i]

            # Check long flag
            flag_name = long_map.get(part.lower()) or short_map.get(part.lower())

            if flag_name:
                flag_def = self.flags[flag_name]
                if flag_def["type"] == bool:
                    result[flag_name] = True
                else:
                    # Consume next token as value
                    if i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                        i += 1
                        result[flag_name] = parts[i]
                    else:
                        result[flag_name] = flag_def["default"]
            else:
                remaining_parts.append(part)

            i += 1

        return result, " ".join(remaining_parts)


def mod_flag_parser() -> FlagParser:
    """Standard flag parser for moderation commands."""
    parser = FlagParser()
    parser.add_flag("silent", short="s", type=bool, default=False)
    parser.add_flag("reason", short="r", type=str, default=None)
    parser.add_flag("notify", short="n", type=bool, default=False)
    return parser


def ban_flag_parser() -> FlagParser:
    """Flag parser for ban command."""
    parser = mod_flag_parser()
    parser.add_flag("delete", short="d", type=str, default=None)
    parser.add_flag("duration", short="t", type=str, default=None)
    return parser


def purge_flag_parser() -> FlagParser:
    """Flag parser for purge command."""
    parser = FlagParser()
    parser.add_flag("user", short="u", type=str, default=None)
    parser.add_flag("contains", short="c", type=str, default=None)
    parser.add_flag("bots", short="b", type=bool, default=False)
    parser.add_flag("embeds", type=bool, default=False)
    parser.add_flag("images", type=bool, default=False)
    parser.add_flag("links", type=bool, default=False)
    return parser
