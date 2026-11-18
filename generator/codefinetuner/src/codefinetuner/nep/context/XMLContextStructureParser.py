from .ContextStructureParserAlgorithm import ContextStructureParserAlgorithm
from .DocumentStructureSupport import parse_xml


class XMLContextStructureParser(ContextStructureParserAlgorithm):
    def parse(self, path, source_text):
        return parse_xml(path, source_text)
