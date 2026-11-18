from .ContextStructureParserAlgorithm import ContextStructureParserAlgorithm
from .DocumentStructureSupport import parse_jsonl


class JSONLContextStructureParser(ContextStructureParserAlgorithm):
    def parse(self, path, source_text):
        return parse_jsonl(path, source_text)
