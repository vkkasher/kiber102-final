import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_generated_openapi_and_types_are_current():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'generate_api_types.py'), '--check'],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_schema_converter_preserves_required_nullable_refs_and_enums():
    from scripts.generate_api_types import render_types
    output = render_types({'Child': {'type': 'object', 'properties': {'id': {'type': 'integer'}}, 'required': ['id']},
                           'Parent': {'type': 'object', 'properties': {
                               'child': {'$ref': '#/components/schemas/Child'},
                               'optional': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
                               'status': {'enum': ['draft', 'published']},
                               'prices': {'type': 'array', 'items': {'type': 'number'}}},
                                      'required': ['child', 'status', 'prices']}})
    assert 'export type Child =' in output
    assert '"child": Child;' in output
    assert '"optional"?: string | null;' in output
    assert '"status": "draft" | "published";' in output
    assert '"prices": Array<number>;' in output
