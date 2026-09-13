"""Real local retrieval, repository isolation and stdio MCP integration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rosetta import cli
from rosetta.references import Catalog, ReferenceRegistry, configuration, discover
from rosetta.tools.tools import ToolError


class ReferencesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / '.git').mkdir()
        (self.root / 'sdk.md').write_text('# Widgets\n\n## Retry semantics\nA revision conflict requires a fresh read before retry.\n\n```js\nreplace(id, body, revision)\n```\n')
        (self.root / 'runtime.md').write_text('# Runtime\n\n## Retry semantics\nA runtime restart repeats a transaction.\n')
        self.config = {'version': 1, 'sources': [
            {'id': 'sdk', 'title': 'Widget SDK', 'path': 'sdk.md', 'file_patterns': ['*.widget'], 'priority': 8},
            {'id': 'runtime', 'title': 'Runtime manual', 'path': 'runtime.md', 'file_patterns': ['*.m'], 'priority': 8},
        ], 'commands': {'test': ['python3', 'check.py']}}
        self.save()

    def save(self):
        (self.root / 'rosetta.json').write_text(json.dumps(self.config))

    def test_search_context_provenance_and_read(self):
        catalog = Catalog(self.root)
        hit = catalog.search('retry semantics', active_file='charge.widget')['results'][0]
        self.assertEqual(hit['source_id'], 'sdk')
        self.assertEqual(hit['start_line'], 3)
        self.assertEqual(len(hit['sha256']), 64)
        self.assertEqual(Path(hit['path']), self.root / 'sdk.md')
        page = catalog.read(hit['document'], hit['start_line'], 2)
        self.assertEqual(page['next_line'], 5)
        self.assertIn('fresh read', page['text'])
        other = catalog.search('retry semantics', active_file='charge.m')['results'][0]
        self.assertEqual(other['source_id'], 'runtime')

    def test_unknown_query_does_not_match_context_alone(self):
        self.assertEqual(Catalog(self.root).search('zyxnotaword', 'charge.widget')['results'], [])

    def test_examples_and_source_filter(self):
        result = Catalog(self.root).search('revision', examples=True)
        self.assertEqual(result['results'][0]['source_id'], 'sdk')
        self.assertTrue(result['results'][0]['example'])
        self.assertEqual(Catalog(self.root).search('revision', source_id='runtime')['results'], [])

    def test_nested_discovery_stops_at_unrelated_repository(self):
        nested = self.root / 'src'
        nested.mkdir()
        self.assertEqual(discover(nested), self.root / 'rosetta.json')
        (nested / '.git').mkdir()
        self.assertIsNone(discover(nested))
        self.assertEqual(Catalog(nested).inventory()['sources'], [])
        config = cli._config(nested)
        self.assertEqual(set(config['mcp']), {'references'})
        self.assertNotIn('MUMPS', json.dumps(config))
        self.assertNotIn('VistA', json.dumps(config))

    def test_docs_refresh_on_next_tool_call(self):
        registry = ReferenceRegistry(self.root)
        self.assertEqual(registry.call('reference_search', {'query': 'newtoken'})['results'], [])
        (self.root / 'sdk.md').write_text('# New contract\nnewtoken means an exact revision.\n')
        self.assertEqual(registry.call('reference_search', {'query': 'newtoken'})['results'][0]['title'], 'New contract')

    def test_bad_arguments_missing_sources_and_path_escape_fail_loudly(self):
        registry = ReferenceRegistry(self.root)
        for args in [{'query': ''}, {'query': 'revision', 'limit': 999}, {'query': 'revision', 'limit': True}, {'query': 'revision', 'source_id': 'missing'}]:
            with self.subTest(args=args), self.assertRaises(ToolError):
                registry.call('reference_search', args)
        with self.assertRaises(ToolError):
            registry.call('reference_read', {'document': '../../private'})
        (self.root / 'sdk.md').unlink()
        with self.assertRaises(ToolError):
            registry.call('reference_search', {'query': 'revision'})

    def test_directory_symlink_cannot_expand_registered_scope(self):
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'escape.md').symlink_to(self.root / 'sdk.md')
        self.config['sources'][0]['path'] = 'docs'
        self.save()
        with self.assertRaisesRegex(ValueError, 'escapes'):
            Catalog(self.root).search('revision')

    def test_invalid_configuration(self):
        self.config['sources'].append(self.config['sources'][0])
        self.save()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            configuration(self.root)

    def test_large_manual_read_is_paginated(self):
        (self.root / 'sdk.md').write_text('# Large manual\n' + '\n'.join(f'revision paragraph {i}' for i in range(1000)))
        catalog = Catalog(self.root)
        hits = catalog.search('revision')
        page = catalog.read(hits['results'][0]['document'], 801, 80)
        self.assertEqual(page['start_line'], 801)
        self.assertEqual(page['end_line'], 880)
        self.assertEqual(page['next_line'], 881)
        self.assertIn('paragraph 799', page['text'])

    def test_real_stdio_server_exposes_and_calls_four_tools(self):
        requests = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'reference_search','arguments':{'query':'revision'}}},
        ]
        run = subprocess.run([sys.executable, '-m', 'rosetta.references', '--project', str(self.root)],
            input=''.join(json.dumps(r)+'\n' for r in requests), capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        responses = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(responses[1]['result']['tools']), 4)
        self.assertFalse(responses[2]['result'].get('isError'))
        self.assertIn('revision', json.dumps(responses[2]['result']))
        self.assertNotIn('MUMPS', responses[0]['result']['instructions'])

    def test_shipped_vendor_docs_and_second_domain(self):
        root = Path(__file__).resolve().parents[1]
        for domain, query, expected in [('payments', 'MUMPS error trapping', 'ydb-errproc'), ('widget-sdk', 'revision conflict', 'widget-sdk')]:
            catalog = Catalog(root / 'examples' / domain)
            result = catalog.search(query)
            self.assertEqual(result['results'][0]['source_id'], expected)
        self.assertEqual(Catalog(root / 'examples/widget-sdk').inventory()['sources'][0]['id'], 'widget-sdk')


if __name__ == '__main__':
    unittest.main()
