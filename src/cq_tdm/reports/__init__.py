"""Report generation modules for CQ TDM."""

# Lazy imports - reportlab is heavy
_LAZY_IMPORTS = {
    "PDFReportGenerator": ".pdf_report",
    "generate_pdf_report": ".pdf_report",
    "generate_report_filename": ".pdf_report",
    "ArtifactInspectionResult": ".pdf_report",
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_name = _LAZY_IMPORTS[name]
        import importlib
        module = importlib.import_module(module_name, __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
