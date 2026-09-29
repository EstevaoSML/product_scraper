"""Language formatting runs without a browser or any external translation service."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('saved', ['en', 'es', 'pt-BR', 'unsupported', 'blocked'])
def check_language_storage_and_dynamic_translation(saved):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for JavaScript translation checks')
    script = Path(__file__).resolve().parents[1] / 'webapp/static/i18n.js'
    program = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const saved = process.argv[2];
const ctx = {localStorage:{getItem(){if(saved === 'blocked')throw Error('denied');return saved;}},
  NodeFilter:{SHOW_TEXT:4},document:{body:{}, createTreeWalker(){return {nextNode(){return false}};}, querySelectorAll(){return [];}}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
assert.equal(vm.runInContext('language',ctx), ['en','es','pt-BR'].includes(saved) ? saved : 'pt-BR');
for(const [lang,count,label,category] of [['en','1 product','Unavailable','Storage'],['es','1 producto','No disponible','Almacenamiento'],['pt-BR','1 produto','Indisponível','Armazenamento']]) {
  vm.runInContext(`language = '${lang}'`,ctx);
  assert.equal(vm.runInContext("tr('1 produto')",ctx),count);
  assert.equal(vm.runInContext("tr('Indisponível')",ctx),label);
  assert.equal(vm.runInContext("tr('Armazenamento')",ctx),category);
  assert.equal(vm.runInContext("tr('Model ABC <img src=x>')",ctx),'Model ABC <img src=x>');
  assert.equal(vm.runInContext("tr('257,39 BRL')",ctx),'257,39 BRL');
}
vm.runInContext("language='en'",ctx);
assert.equal(vm.runInContext("tr('Média atual · 2 varejistas')",ctx),'Current average · 2 retailers');
vm.runInContext("language='es'",ctx);
assert.equal(vm.runInContext("tr('Vendedor: Não informado na evidência')",ctx),'Vendedor: No indicado en la evidencia');
"""
    result = subprocess.run([node, '-e', program, str(script), saved],capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode == 0, result.stderr
