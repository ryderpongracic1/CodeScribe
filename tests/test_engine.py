"""Test suite for CodeScribe engine-core: parser, updater, and orchestrator."""
import unittest
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from textwrap import dedent

import networkx as nx

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codescribe import parser, updater
from codescribe.orchestrator import DocstringOrchestrator


FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'sample_project'


class TestGraphDirection(unittest.TestCase):
    """Test (a): dependency order — b documented before a when a imports b."""

    def setUp(self):
        """Create a temporary project with a->b import relationship."""
        self.tmpdir = Path(tempfile.mkdtemp())
        # b.py has no imports — it's the dependency
        (self.tmpdir / 'b.py').write_text(dedent('''\
            def helper():
                return 42
        '''))
        # a.py imports b — it's the importer
        (self.tmpdir / 'a.py').write_text(dedent('''\
            from . import b

            def main():
                return b.helper()
        '''))
        # __init__.py for relative import resolution
        (self.tmpdir / '__init__.py').write_text('')

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_dependency_documented_before_importer(self):
        """b.py must appear before a.py in topological order."""
        files = [self.tmpdir / 'a.py', self.tmpdir / 'b.py', self.tmpdir / '__init__.py']
        graph = parser.build_dependency_graph(files, self.tmpdir, log_callback=lambda m: None)

        # Graph must be a DAG
        self.assertTrue(nx.is_directed_acyclic_graph(graph))

        doc_order = list(nx.topological_sort(graph))
        b_idx = doc_order.index(self.tmpdir / 'b.py')
        a_idx = doc_order.index(self.tmpdir / 'a.py')
        self.assertLess(b_idx, a_idx,
                        "Dependency (b.py) must be documented before importer (a.py)")

    def test_predecessors_are_dependencies(self):
        """graph.predecessors(a.py) should return b.py (a's dependency)."""
        files = [self.tmpdir / 'a.py', self.tmpdir / 'b.py', self.tmpdir / '__init__.py']
        graph = parser.build_dependency_graph(files, self.tmpdir, log_callback=lambda m: None)

        a_deps = list(graph.predecessors(self.tmpdir / 'a.py'))
        self.assertIn(self.tmpdir / 'b.py', a_deps)

        # b has no internal dependencies
        b_deps = list(graph.predecessors(self.tmpdir / 'b.py'))
        self.assertEqual(b_deps, [])


class TestPlainImportEdges(unittest.TestCase):
    """Test (b): plain import statements (ast.Import) create edges."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / 'base.py').write_text('def base_func(): pass\n')
        # Uses plain import (not from-import)
        (self.tmpdir / 'user.py').write_text(dedent('''\
            import base

            def use_it():
                return base.base_func()
        '''))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_plain_import_creates_edge(self):
        """'import base' should create an edge from base.py to user.py."""
        files = [self.tmpdir / 'base.py', self.tmpdir / 'user.py']
        graph = parser.build_dependency_graph(files, self.tmpdir, log_callback=lambda m: None)

        # Edge direction: dependency -> importer
        self.assertTrue(graph.has_edge(self.tmpdir / 'base.py', self.tmpdir / 'user.py'))

    def test_plain_import_dotted_module(self):
        """'import pkg.mod' should resolve to pkg/mod.py."""
        pkg_dir = self.tmpdir / 'pkg'
        pkg_dir.mkdir()
        (pkg_dir / '__init__.py').write_text('')
        (pkg_dir / 'mod.py').write_text('X = 1\n')
        (self.tmpdir / 'importer.py').write_text('import pkg.mod\n')

        files = [pkg_dir / '__init__.py', pkg_dir / 'mod.py', self.tmpdir / 'importer.py']
        graph = parser.build_dependency_graph(files, self.tmpdir, log_callback=lambda m: None)

        self.assertTrue(graph.has_edge(pkg_dir / 'mod.py', self.tmpdir / 'importer.py'))


class TestUpdaterPreservesFormatting(unittest.TestCase):
    """Test (c): updater preserves comments/blank lines byte-for-byte."""

    def test_comments_and_blanks_preserved(self):
        """All comments and blank line structure must survive docstring insertion."""
        source = dedent('''\
            # Module header comment
            # Second line of header

            import os

            # Function comment block
            def my_func(x):
                # Internal logic comment
                y = x + 1

                # Another internal comment
                return y


            # Between-function comment
            class MyClass:
                # Class body comment
                def method(self):
                    pass
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_file_with_docstrings(
            tmp,
            {'my_func': 'Does math.', 'MyClass.method': 'A method.'},
            log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        # All original comments must be preserved
        for comment in [
            '# Module header comment',
            '# Second line of header',
            '# Function comment block',
            '# Internal logic comment',
            '# Another internal comment',
            '# Between-function comment',
            '# Class body comment',
        ]:
            self.assertIn(comment, result, f"Comment lost: {comment}")

        # Blank line structure preserved (two newlines between func and class)
        self.assertIn('\n\n\n# Between-function comment', result)

    def test_byte_for_byte_outside_docstrings(self):
        """Content outside docstring locations must be identical."""
        source = dedent('''\
            import sys

            def func():
                x = 1  # trailing comment
                return x
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_file_with_docstrings(
            tmp,
            {'func': 'My docstring.'},
            log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        # The import and trailing comment must be byte-identical
        self.assertIn('import sys', result)
        self.assertIn('x = 1  # trailing comment', result)
        self.assertIn('    return x\n', result)


class TestDocstringReplacementVsInsertion(unittest.TestCase):
    """Test (d): docstring replacement vs insertion."""

    def test_existing_docstring_replaced(self):
        """An existing docstring should be replaced, not duplicated."""
        source = dedent('''\
            def func():
                """Old docstring."""
                return 1
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_file_with_docstrings(
            tmp, {'func': 'New docstring.'}, log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        self.assertNotIn('Old docstring', result)
        self.assertIn('New docstring.', result)
        # Should only have one docstring
        self.assertEqual(result.count('"""'), 2)  # opening and closing

    def test_no_docstring_inserts_one(self):
        """A function without a docstring gets one inserted."""
        source = dedent('''\
            def func():
                return 1
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_file_with_docstrings(
            tmp, {'func': 'Inserted docstring.'}, log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        self.assertIn('Inserted docstring.', result)
        self.assertIn('    return 1', result)

    def test_module_docstring_replacement(self):
        """Module-level docstring replacement."""
        source = dedent('''\
            """Old module doc."""
            import os

            x = 1
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_module_docstring(
            tmp, 'New module doc.', log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        self.assertNotIn('Old module doc', result)
        self.assertIn('New module doc.', result)
        self.assertIn('import os', result)

    def test_module_docstring_insertion(self):
        """Module without docstring gets one inserted."""
        source = dedent('''\
            import os

            x = 1
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_module_docstring(
            tmp, 'Brand new module doc.', log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        self.assertIn('Brand new module doc.', result)
        self.assertIn('import os', result)


class TestDecoratedAndAsyncDefs(unittest.TestCase):
    """Test (e): decorated + async defs."""

    def test_decorated_function(self):
        """Docstring inserted after def line, not after decorator."""
        source = dedent('''\
            from functools import wraps

            def deco(f):
                @wraps(f)
                def w(*a): return f(*a)
                return w

            @deco
            def my_func(x):
                return x
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_file_with_docstrings(
            tmp, {'my_func': 'Decorated fn doc.'}, log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        lines = result.splitlines()
        # Find the decorator, def, and docstring lines
        deco_line = next(i for i, l in enumerate(lines) if l.strip() == '@deco')
        def_line = next(i for i, l in enumerate(lines) if 'def my_func' in l)
        doc_line = next(i for i, l in enumerate(lines) if 'Decorated fn doc.' in l)

        self.assertLess(deco_line, def_line)
        self.assertEqual(doc_line, def_line + 1,
                         "Docstring must be immediately after def line")

    def test_async_function(self):
        """Docstring works for async def."""
        source = dedent('''\
            import asyncio

            async def async_fn():
                await asyncio.sleep(0)
                return 1
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_file_with_docstrings(
            tmp, {'async_fn': 'Async function doc.'}, log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        self.assertIn('Async function doc.', result)
        self.assertIn('async def async_fn()', result)
        # Docstring is inside the function
        lines = result.splitlines()
        def_idx = next(i for i, l in enumerate(lines) if 'async def async_fn' in l)
        doc_idx = next(i for i, l in enumerate(lines) if 'Async function doc.' in l)
        self.assertEqual(doc_idx, def_idx + 1)

    def test_async_with_existing_docstring(self):
        """Replace existing docstring on async function."""
        source = dedent('''\
            async def coro():
                """Old coro doc."""
                return 42
        ''')

        tmp = Path(tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False).name)
        tmp.write_text(source)

        updater.update_file_with_docstrings(
            tmp, {'coro': 'New coro doc.'}, log_callback=lambda m: None
        )

        result = tmp.read_text()
        tmp.unlink()

        self.assertNotIn('Old coro doc', result)
        self.assertIn('New coro doc.', result)


class TestIntegrationWithStubbedLLM(unittest.TestCase):
    """Integration test: end-to-end with stubbed LLMHandler.

    Verifies the full pipeline: scan -> parse -> document in order -> update files.
    """

    def setUp(self):
        """Create a small project with known import structure."""
        self.tmpdir = Path(tempfile.mkdtemp())
        self.project = self.tmpdir / 'myproject'
        self.project.mkdir()

        # base.py — no imports, should be documented FIRST
        (self.project / 'base.py').write_text(dedent('''\
            # Base module comment
            def base_func(x):
                return x * 2
        '''))

        # consumer.py — imports base, should be documented SECOND
        (self.project / 'consumer.py').write_text(dedent('''\
            # Consumer module comment
            from . import base

            def consume(val):
                return base.base_func(val) + 1
        '''))

        (self.project / '__init__.py').write_text('')

        self.documented_order = []

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _make_fake_llm(self):
        """Create a fake LLMHandler that returns predictable docstrings."""
        fake = MagicMock()
        call_count = [0]

        def fake_generate_doc(prompt):
            call_count[0] += 1
            # Determine which file is being documented from the File Path line
            if 'File Path: `base.py`' in prompt:
                self.documented_order.append('base.py')
                return {
                    '__module__': 'Base module provides base_func.',
                    'base_func': 'Multiplies x by 2.',
                }
            elif 'File Path: `consumer.py`' in prompt:
                self.documented_order.append('consumer.py')
                # The prompt should contain base's docs as context
                self.consumer_prompt = prompt
                return {
                    '__module__': 'Consumer module wraps base.',
                    'consume': 'Adds 1 to base result.',
                }
            elif '__init__.py' in prompt:
                self.documented_order.append('__init__.py')
                return {
                    '__module__': 'Package init.',
                }
            return {'__module__': 'Unknown.'}

        def fake_generate_text(prompt):
            return 'Package summary.'

        fake.generate_documentation = fake_generate_doc
        fake.generate_text_response = fake_generate_text
        fake.progress_callback = lambda m: None
        return fake

    def test_end_to_end_pipeline(self):
        """Full pipeline documents dependencies first with correct context."""
        fake_llm = self._make_fake_llm()
        self.consumer_prompt = ''

        orch = DocstringOrchestrator(
            path_or_url=str(self.project),
            description='A test project.',
            exclude=[],
            llm_handler=fake_llm,
            progress_callback=lambda e, d: None,
        )
        orch.run()

        # (a) base.py documented before consumer.py
        self.assertIn('base.py', self.documented_order)
        self.assertIn('consumer.py', self.documented_order)
        base_idx = self.documented_order.index('base.py')
        consumer_idx = self.documented_order.index('consumer.py')
        self.assertLess(base_idx, consumer_idx,
                        "base.py must be documented before consumer.py")

        # (b) consumer's prompt contains base's docstrings as context
        self.assertIn('base_func', self.consumer_prompt,
                      "Consumer's prompt must include base's dependency context")
        self.assertIn('Multiplies x by 2', self.consumer_prompt,
                      "Consumer's prompt must contain base_func's docstring")

        # (c) Source files are updated with docstrings
        base_content = (self.project / 'base.py').read_text()
        self.assertIn('Multiplies x by 2.', base_content)

        consumer_content = (self.project / 'consumer.py').read_text()
        self.assertIn('Adds 1 to base result.', consumer_content)

        # (d) Comments are preserved
        self.assertIn('# Base module comment', base_content)
        self.assertIn('# Consumer module comment', consumer_content)

    def test_cyclic_graph_handled(self):
        """Cyclic imports don't crash — falls back to file list order."""
        # Make a cycle: a imports b, b imports a
        (self.project / 'base.py').write_text('from . import consumer\n')
        (self.project / 'consumer.py').write_text('from . import base\n')

        fake_llm = self._make_fake_llm()
        orch = DocstringOrchestrator(
            path_or_url=str(self.project),
            description='Cyclic project.',
            exclude=[],
            llm_handler=fake_llm,
            progress_callback=lambda e, d: None,
        )
        # Should not raise
        orch.run()


if __name__ == '__main__':
    unittest.main()
