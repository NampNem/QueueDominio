"""
Aqui você pluga as macros reais.
executar_macro() deve gerar o arquivo dentro de `pasta` e devolver o Path dele.
"""
import re
import time
from datetime import datetime
from pathlib import Path


def _limpo(txt: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", txt).strip("_")


def executar_macro(servico: str, empresa: str, solicitante: str, pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)

    # ---- EXEMPLO PARA MACRO DE EXCEL (Windows, precisa de pywin32) ----
    # import win32com.client
    # excel = win32com.client.Dispatch("Excel.Application")
    # excel.Visible = False
    # wb = excel.Workbooks.Open(r"C:\macros\relatorios.xlsm")
    # excel.Run("ModuloRelatorios.GerarRelatorio", servico, empresa, str(pasta))
    # wb.Close(False); excel.Quit()
    # return next(pasta.glob("*.xlsx"))

    # ---- PLACEHOLDER (simula o processamento) ----
    time.sleep(8)
    destino = pasta / f"{_limpo(servico)}_{_limpo(empresa)}_{datetime.now():%H%M%S}.csv"
    destino.write_text(
        f"servico;empresa;solicitante;gerado_em\n"
        f"{servico};{empresa};{solicitante};{datetime.now():%d/%m/%Y %H:%M:%S}\n",
        encoding="utf-8-sig",
    )
    return destino
