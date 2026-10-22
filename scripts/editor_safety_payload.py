"""Ship the same tested editor handlers to retained native Mac pages."""
import ast
import hashlib
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _literal_function_source(tree, function_name, starts_with):
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == function_name)
    return next(node.value for node in ast.walk(function)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith(starts_with))


def editor_safety_payload(repo=REPO):
    repo = Path(repo)
    source = repo / 'skills/story-skill/scripts/story_workbench.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    script = _literal_function_source(tree, '_editor_page', 'const TOKEN=')
    tools = _literal_function_source(tree, '_editor_tools_script', '\nfunction editorDialog(')
    identity = script[script.index('function documentIdentity('):script.index('function documentLocation(')].strip()
    badge = script[script.index('function badge('):script.index('function show(d)')].strip().replace('function badge(', 'function shippedBadge(', 1)
    pending = script[script.index('function pendingEdits('):script.index('function item(')].strip().replace('function pendingEdits(', 'function shippedPending(', 1)
    recovery = script[script.index('async function recover('):script.index("$('text').addEventListener('input'")].strip()
    versions = script[script.index('function chapterChoices('):script.index('function primaryChapterDocument(')].strip()
    save = script[script.index('async function saveDocument('):script.index("$('save').onclick")].strip()
    save_all = script[script.index("$('save-all').onclick="):script.index("$('compare').onclick")].strip()
    opening = script[script.index('async function openDoc('):script.index('function pendingEdits(')].strip()
    reload = script[script.index('async function reloadDocuments('):script.index("$('refresh').onclick")].strip()
    comparison = tools[tools.index("$('compare-choose').onclick="):].strip()
    writing = tools[tools.index("$('writing-task').onclick="):tools.index("$('compare-choose').onclick=")].strip()
    review = script[script.index("$('review-task').onclick="):script.index("$('copy-review').onclick=")].strip()
    download = script[script.index('function downloadDocument('):script.index("$('download').onclick=")].strip()
    template = (repo / 'desktop/macos/editor-safety.js.in').read_text(encoding='utf-8')
    version = hashlib.sha256((identity + badge + pending + recovery + versions + save + save_all + opening + reload + comparison + writing + review + download + template).encode()).hexdigest()
    return (template.replace('__SAFETY_VERSION__', version)
            .replace('/*__DOCUMENT_IDENTITY__*/', identity)
            .replace('/*__SAVE_STATUS__*/', badge + '\n' + pending)
            .replace('/*__RECOVER_DOCUMENT__*/', recovery)
            .replace('/*__VERSION_NAVIGATION__*/', versions)
            .replace('/*__SAVE_DOCUMENT__*/', save)
            .replace('/*__SAVE_ALL__*/', save_all)
            .replace('/*__OPEN_DOCUMENT__*/', opening)
            .replace('/*__RELOAD_DOCUMENTS__*/', reload)
            .replace('/*__COMPARE_CHOOSE__*/', comparison)
            .replace('/*__WRITING_TASK__*/', writing)
            .replace('/*__REVIEW_TASK__*/', review)
            .replace('/*__DOWNLOAD_DOCUMENT__*/', download))
