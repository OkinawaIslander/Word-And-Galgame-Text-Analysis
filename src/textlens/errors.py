"""Project-specific exceptions."""


class TextLensError(Exception):
    """Base exception that the CLI can present without a traceback."""


class UnsupportedFormat(TextLensError):
    """The file extension or content is not supported (yet)."""


class ParseError(TextLensError):
    """A supported file could not be parsed."""
