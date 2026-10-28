"""Lightweight checks runnable offline even without Flask/Werkzeug installed."""
import ast
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import create_engine, text

ROOT=Path(__file__).resolve().parents[1]


def source():
    return (ROOT/'app/main.py').read_text(encoding='utf-8')


def test_user_schema_and_existing_user_migration_are_additive():
    tree=ast.parse(source())
    ddl=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
             and any(isinstance(target,ast.Name) and target.id=='DDL' for target in node.targets))
    db=create_engine('sqlite:///:memory:')
    with db.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement.replace('BIGSERIAL PRIMARY KEY','INTEGER PRIMARY KEY AUTOINCREMENT')))
        for key in ('is_active','auth_version','created_at','last_login'):
            assert key in {r[1] for r in conn.execute(text('PRAGMA table_info(users)'))}
        conn.execute(text("INSERT INTO users(email,password_hash,role,updated_at) VALUES('admin@example.com','HASH','admin','now')"))
        row=conn.execute(text('SELECT is_active,auth_version,role FROM users WHERE email=:email'),
                         {'email':'admin@example.com'}).one()
        assert row == (1,1,'admin')


def test_login_and_user_templates_compile():
    environment=Environment(loader=FileSystemLoader(str(ROOT/'app/templates')))
    for name in ('login.html','index.html','users.html'):
        environment.get_template(name)


def test_post_forms_have_csrf_fields_and_fetch_token():
    from bs4 import BeautifulSoup
    for name in ('login.html','index.html','users.html'):
        html=(ROOT/'app/templates'/name).read_text(encoding='utf-8')
        tree=BeautifulSoup(html, 'html.parser')
        for form in tree.find_all('form'):
            if form.get('method','GET').lower() == 'post':
                assert form.find('input',attrs={'name':'csrf_token'}), name
    assert 'X-CSRF-Token' in (ROOT/'app/templates/index.html').read_text(encoding='utf-8')


def test_auth_from_db_not_session_role():
    code=source()
    assert "session.get('role')" not in code
    assert 'auth_version' in code
    assert 'is_active' in code
    assert "sync_users()" not in code
    assert "legacy_user_bootstrap_v1" in code
