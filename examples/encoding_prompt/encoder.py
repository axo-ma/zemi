from pathlib import Path


def encode(workbook_path: str | Path, worksheet_name: str, *, format: str) -> str:
    from openpyxl import load_workbook
    if format not in {"cells", "cells_compact"}:
        raise ValueError(f"Unknown encoding format: {format}")
    separator = "=" if format == "cells_compact" else " = "
    workbook = load_workbook(workbook_path, data_only=True)
    try:
        sheet = workbook[worksheet_name]
        return "\n".join(f"{cell.coordinate}{separator}{cell.value}"
                         for row in sheet for cell in row if cell.value is not None)
    finally:
        workbook.close()
