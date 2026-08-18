"""This module provides functionality to update Python files with new docstrings.

Uses libcst for source-preserving transformations — comments, blank lines, and
formatting outside the inserted/replaced docstrings are left byte-for-byte intact.
"""
import libcst as cst
from pathlib import Path
from typing import Dict, Callable


def _no_op_log(message: str):
    """Helper no-op function for default callback."""
    pass


def _make_docstring_node(text: str, indent: str) -> cst.SimpleStatementLine:
    """Create a libcst node for a docstring with proper indentation.

    Handles triple-quote escaping: if text contains triple double-quotes,
    switches to triple single-quotes. If it contains both, escapes the
    double-quote variant.
    """
    if '"""' in text:
        if "'''" in text:
            # Both quote styles present — escape the double-quote triples
            text = text.replace('"""', '\\"\\"\\"')
            quote = '"""'
        else:
            quote = "'''"
    else:
        quote = '"""'

    # Multi-line docstrings get newlines after opening and before closing quotes
    if '\n' in text:
        formatted = f"{quote}\n{text}\n{indent}{quote}"
    else:
        formatted = f"{quote}{text}{quote}"

    return cst.SimpleStatementLine(
        body=[cst.Expr(value=cst.ConcatenatedString(
            left=cst.SimpleString(value=formatted),
            right=cst.SimpleString(value=""),
        ) if False else cst.SimpleString(value=formatted))],
        leading_lines=[],
    )


class _DocstringTransformer(cst.CSTTransformer):
    """CST transformer that inserts or replaces docstrings on functions/classes."""

    def __init__(self, docstrings: Dict[str, str]):
        super().__init__()
        self.docstrings = docstrings
        self._class_stack: list = []

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._class_stack.append(node.name.value)
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        class_name = self._class_stack.pop()
        if class_name in self.docstrings:
            updated_node = self._apply_docstring(updated_node, self.docstrings[class_name])
        return updated_node

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        name = updated_node.name.value
        if self._class_stack:
            key = f"{self._class_stack[-1]}.{name}"
        else:
            key = name

        if key in self.docstrings:
            updated_node = self._apply_docstring(updated_node, self.docstrings[key])
        return updated_node

    def _apply_docstring(self, node, docstring_text: str):
        """Insert or replace a docstring on a class/function node."""
        body = node.body
        if not isinstance(body, cst.IndentedBlock):
            return node

        indent = body.indent or "    "
        new_doc = _make_docstring_node(docstring_text, indent)

        stmts = list(body.body)
        if stmts and self._is_docstring(stmts[0]):
            # Replace existing docstring
            stmts[0] = new_doc
        else:
            # Insert before existing body
            stmts.insert(0, new_doc)

        return node.with_changes(body=body.with_changes(body=stmts))

    def _is_docstring(self, stmt) -> bool:
        """Check if a statement is a string-expression (docstring)."""
        if isinstance(stmt, cst.SimpleStatementLine):
            if len(stmt.body) == 1 and isinstance(stmt.body[0], cst.Expr):
                val = stmt.body[0].value
                if isinstance(val, (cst.SimpleString, cst.ConcatenatedString, cst.FormattedString)):
                    return True
        return False


class _ModuleDocstringTransformer(cst.CSTTransformer):
    """CST transformer that inserts or replaces the module-level docstring."""

    def __init__(self, docstring: str):
        super().__init__()
        self.docstring = docstring
        self._done = False

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if self._done:
            return updated_node
        self._done = True

        new_doc = _make_docstring_node(self.docstring, "")
        stmts = list(updated_node.body)

        if stmts and self._is_module_docstring(stmts[0]):
            stmts[0] = new_doc
        else:
            stmts.insert(0, new_doc)

        return updated_node.with_changes(body=stmts)

    def _is_module_docstring(self, stmt) -> bool:
        """Check if a statement is a string-expression (module docstring)."""
        if isinstance(stmt, cst.SimpleStatementLine):
            if len(stmt.body) == 1 and isinstance(stmt.body[0], cst.Expr):
                val = stmt.body[0].value
                if isinstance(val, (cst.SimpleString, cst.ConcatenatedString, cst.FormattedString)):
                    return True
        return False


def update_file_with_docstrings(file_path: Path, docstrings: Dict[str, str], log_callback: Callable[[str], None] = print):
    """Parses a Python file, inserts/replaces docstrings for functions/classes,
    and writes the file back preserving all other formatting."""
    if not docstrings:
        return
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source_code = f.read()

        tree = cst.parse_module(source_code)
        transformer = _DocstringTransformer(docstrings)
        new_tree = tree.visit(transformer)
        new_source = new_tree.code

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)
        log_callback(f'Successfully updated {file_path.name} with new docstrings.')
    except Exception as e:
        log_callback(f'Error updating file {file_path.name}: {e}')


def update_module_docstring(file_path: Path, docstring: str, log_callback: Callable[[str], None] = print):
    """Parses a Python file, adds or replaces the module-level docstring,
    and writes the file back preserving all other formatting."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source_code = f.read()

        tree = cst.parse_module(source_code)
        transformer = _ModuleDocstringTransformer(docstring)
        new_tree = tree.visit(transformer)
        new_source = new_tree.code

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)
        log_callback(f'Successfully added/updated module docstring for {file_path.name}.')
    except Exception as e:
        log_callback(f'Error updating module docstring for {file_path.name}: {e}')
