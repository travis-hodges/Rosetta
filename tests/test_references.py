"""Real local retrieval, repository isolation and stdio MCP integration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rosetta import cli
from rosetta.references import (
    Catalog,
    ReferenceRegistry,
    add_reference,
    configuration,
    discover,
    pending_requests,
    request_reference,
)
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

    def test_missing_language_request_is_visible_and_cleared_after_import(self):
        (self.root / 'rosetta.json').chmod(0o640)
        state = request_reference(self.root, 'JOVIAL', 'editing a flight-control module')
        self.assertTrue(state.is_file())
        inventory = Catalog(self.root).inventory()
        self.assertEqual(inventory['state'], 'needs-source')
        self.assertEqual(inventory['pending_requests'], [{
            'language': 'JOVIAL', 'reason': 'editing a flight-control module',
        }])

        with tempfile.TemporaryDirectory() as upload:
            manual = Path(upload) / 'jovial-manual.md'
            manual.write_text('# JOVIAL procedures\n\nDEFINE declares a procedure.\n', encoding='utf-8')
            location, source = add_reference(
                self.root, manual, language='JOVIAL', origin='https://standards.example/jovial',
                file_patterns=('*.jov',),
            )

        self.assertEqual(location, self.root / 'rosetta.json')
        self.assertEqual(source.id, 'jovial')
        self.assertEqual(source.language, 'JOVIAL')
        self.assertEqual(source.origin, 'https://standards.example/jovial')
        self.assertTrue((self.root / source.path).is_file())
        self.assertEqual(pending_requests(self.root), [])
        self.assertEqual(Catalog(self.root).inventory()['state'], 'ready')
        self.assertEqual(Catalog(self.root).search('DEFINE procedure')['results'][0]['source_id'], 'jovial')
        saved = json.loads((self.root / 'rosetta.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['commands'], self.config['commands'])
        self.assertEqual((self.root / 'rosetta.json').stat().st_mode & 0o777, 0o640)

    def test_add_creates_manifest_and_registers_in_project_file_without_copy(self):
        project = self.root / 'new-project'
        project.mkdir()
        (project / '.git').mkdir()
        manual = project / 'manual.txt'
        manual.write_text('CMS-2 ARRAY declaration rules', encoding='utf-8')
        location, source = add_reference(project, manual, language='CMS-2')
        self.assertEqual(location, project / 'rosetta.json')
        self.assertEqual(source.path, 'manual.txt')
        self.assertEqual(json.loads(location.read_text())['sources'][0]['origin'], 'user-provided')

    def test_failed_import_does_not_change_manifest_or_leave_copied_files(self):
        before = (self.root / 'rosetta.json').read_bytes()
        with tempfile.TemporaryDirectory() as upload:
            unsupported = Path(upload) / 'manual.pdf'
            unsupported.write_bytes(b'%PDF-1.4')
            with self.assertRaisesRegex(ValueError, 'no .md'):
                add_reference(self.root, unsupported, language='JOVIAL')
        self.assertEqual((self.root / 'rosetta.json').read_bytes(), before)
        self.assertFalse((self.root / 'references' / 'uploaded' / 'jovial.pdf').exists())

    def test_catalog_validation_failure_rolls_back_manifest_and_external_copy(self):
        before = (self.root / 'rosetta.json').read_bytes()
        (self.root / 'runtime.md').unlink()
        with tempfile.TemporaryDirectory() as upload:
            manual = Path(upload) / 'jovial.md'
            manual.write_text('# JOVIAL\nA valid UTF-8 manual.\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'runtime does not exist'):
                add_reference(self.root, manual, language='JOVIAL')
        self.assertEqual((self.root / 'rosetta.json').read_bytes(), before)
        self.assertFalse((self.root / 'references' / 'uploaded' / 'jovial.md').exists())

    def test_agent_prompt_asks_before_sourcing_missing_material(self):
        prompt = cli._config(self.root)['agent']['rosetta']['prompt']
        self.assertIn('ask one concise question', prompt)
        self.assertIn('do not guess or fetch material yet', prompt)
        self.assertIn('rosetta references add', prompt)

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
