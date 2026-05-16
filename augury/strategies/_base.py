from dataclasses import dataclass
import pandas as pd


@dataclass
class Strategy:
    name: str
    symbol: str
    description: str

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return DataFrame indexed by date with bool columns ['entry', 'exit']."""
        raise NotImplementedError
