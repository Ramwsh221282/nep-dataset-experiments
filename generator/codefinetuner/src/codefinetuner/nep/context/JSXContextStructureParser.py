"""JSX context strategy."""
from .JavascriptStructureSupport import JavascriptStructureSupport


class JSXContextStructureParser(JavascriptStructureSupport):
    language = "javascript"
    format = "jsx"
    jsx = True
