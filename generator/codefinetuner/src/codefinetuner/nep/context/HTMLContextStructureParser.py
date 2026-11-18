from .ContextStructureParserAlgorithm import ContextStructureParserAlgorithm
from .DocumentStructureSupport import parse_html


class HTMLContextStructureParser(ContextStructureParserAlgorithm):
    def parse(self, path, source_text):
        return parse_html(path, source_text)
