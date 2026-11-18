from .ContextStructureParserAlgorithm import ContextStructureParserAlgorithm
from .DocumentStructureSupport import parse_toml


class TOMLContextStructureParser(ContextStructureParserAlgorithm):
    def parse(self, path, source_text):
        return parse_toml(path, source_text)
