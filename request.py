from dataclasses import dataclass


@dataclass(frozen=True)
class Request:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes

    @property
    def path(self) -> str:
        return self.target.partition('?')[0]
    
    @property
    def query(self) -> str:
        return self.target.partition('?')[2]

    def header(self, name: str, default: str | None = None) -> str | None:
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return default
