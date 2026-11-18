"""TSX context strategy."""
from .JavascriptStructureSupport import JavascriptStructureSupport


class TSXContextStructureParser(JavascriptStructureSupport):
    language = "tsx"
    format = "tsx"
    jsx = True
