from .ContextStructureParserAlgorithm import ContextStructureParserAlgorithm
from .DocumentStructureSupport import parse_json


class JSONContextStructureParser(ContextStructureParserAlgorithm):
    def parse(self, path, source_text):
        return parse_json(path, source_text, 'json')
