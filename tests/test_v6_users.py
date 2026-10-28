"""HTTP integration tests for the user console. Requires Flask/Werkzeug."""
import importlib

import pytest

pytest.importorskip('flask')
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text


@pytest.fixture
def site(monkeypatch, tmp_path):
    monkeypatch.setenv('ADMIN_PASSWORD_1', 'InitialAdminPass!2026')
    monkeypatch.delenv('ADMIN_PASSWORD_2', raising=False)
    monkeypatch.setenv('USER_PASSWORD_1', 'InitialViewerPass!2026')
    main = importlib.import_module('app.main')
    local_engine = create_engine(f'sqlite:///{tmp_path}/users.db')
    monkeypatch.setattr(main, 'engine', local_engine)
    monkeypatch.setattr(main, '_INITIALIZED_ENGINE', None)
    main.app.config.update(TESTING=True, SECRET_KEY='test-session-signature')
    client = main.app.test_client()
    yield main, client
    local_engine.dispose()


def login(main, client, email, password):
    page = client.get('/login')
    csrf = BeautifulSoup(page.data, 'html.parser').find('input', {'name':'csrf_token'})['value']
    return client.post('/login', data={'email':email,'password':password,
                                       'csrf_token':csrf}, follow_redirects=False)


def token(client):
    with client.session_transaction() as sess:
        return sess['csrf_token']


def test_admin_can_add_and_manage_users(site):
    main, client = site
    assert login(main, client, 'juanjose.solanocamacho@noventiq.com', 'InitialAdminPass!2026').status_code == 302
    page = client.get('/admin/users')
    assert page.status_code == 200
    assert b'Crear usuario' in page.data
    response = client.post('/admin/users/create', data={'email':'person@example.com',
        'password':'SafePassword#2026', 'role':'user','csrf_token':token(client)})
    assert response.status_code == 302
    with main.engine.connect() as conn:
        account = conn.execute(text('SELECT * FROM users WHERE email=:email'),
                               {'email':'person@example.com'}).mappings().one()
    assert account['role'] == 'user'
    assert account['password_hash'] != 'SafePassword#2026'
    assert account['is_active'] == 1
    assert client.post('/admin/users/role', data={'email':'person@example.com',
        'role':'admin', 'csrf_token':token(client)}).status_code == 302
    assert client.post('/admin/users/status', data={'email':'person@example.com',
        'active':'0','csrf_token':token(client)}).status_code == 302
    with main.engine.connect() as conn:
        role, active = conn.execute(text('SELECT role,is_active FROM users WHERE email=:email'),
                                    {'email':'person@example.com'}).one()
    assert (role,active) == ('admin',0)
    # Render env roles must not override administrator choices or reactivate accounts.
    main.bootstrap_users()
    with main.engine.connect() as conn:
        assert conn.execute(text('SELECT is_active FROM users WHERE email=:email'),
                            {'email':'person@example.com'}).scalar_one() == 0


def test_csrf_and_non_admin_restrictions(site):
    main, client = site
    assert login(main, client, 'juanjose.solanocamacho@noventiq.com', 'InitialAdminPass!2026').status_code == 302
    assert client.post('/admin/users/create', data={'email':'b@example.com',
             'password':'SafePassword#2026','role':'admin'}).status_code == 400
    with main.engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM users WHERE email=:email'),
                             {'email':'b@example.com'}).scalar_one() == 0
    client.get('/logout')
    assert login(main, client, 'alonso.fallasvargas@noventiq.com', 'InitialViewerPass!2026').status_code == 302
    assert client.get('/admin/users').status_code == 403
    assert client.post('/admin/users/create', data={'email':'b@example.com',
             'password':'SafePassword#2026','role':'admin','csrf_token':token(client)}).status_code == 403


def test_admin_can_disable_legacy_user_without_seeding_it_again(site):
    main, client = site
    assert login(main, client, 'juanjose.solanocamacho@noventiq.com', 'InitialAdminPass!2026').status_code == 302
    target='alonso.fallasvargas@noventiq.com'
    client.post('/admin/users/status', data={'email':target,'active':'0',
                                              'csrf_token':token(client)})
    main.bootstrap_users()
    with main.engine.connect() as conn:
        assert conn.execute(text('SELECT is_active FROM users WHERE email=:email'),
                            {'email':target}).scalar_one() == 0
    client.get('/logout')
    response=login(main,client,target,'InitialViewerPass!2026')
    assert response.status_code == 200
    assert b'Credenciales incorrectas' in response.data


def test_password_change_invalidates_other_sessions(site):
    main, admin = site
    assert login(main,admin,'juanjose.solanocamacho@noventiq.com','InitialAdminPass!2026').status_code == 302
    admin.post('/admin/users/create', data={'email':'other@example.com',
        'password':'OldPassword#2026','role':'user','csrf_token':token(admin)})
    viewer=main.app.test_client()
    assert login(main,viewer,'other@example.com','OldPassword#2026').status_code == 302
    assert viewer.get('/').status_code == 200
    admin.post('/admin/users/password',data={'email':'other@example.com',
        'password':'NewPassword#2026','csrf_token':token(admin)})
    assert viewer.get('/').status_code == 302
    viewer.get('/logout')
    assert login(main,viewer,'other@example.com','OldPassword#2026').status_code == 200
    assert login(main,viewer,'other@example.com','NewPassword#2026').status_code == 302


def test_admin_cannot_disable_self(site):
    main, client = site
    email='juanjose.solanocamacho@noventiq.com'
    assert login(main,client,email,'InitialAdminPass!2026').status_code == 302
    client.post('/admin/users/status',data={'email':email,'active':'0',
                                            'csrf_token':token(client)})
    with main.engine.connect() as conn:
        assert conn.execute(text('SELECT is_active FROM users WHERE email=:email'),
                            {'email':email}).scalar_one() == 1


def test_migration_preserves_v5_users_and_does_not_override_role(monkeypatch, tmp_path):
    monkeypatch.setenv('ADMIN_PASSWORD_1','InitAdminPass!2026')
    monkeypatch.setenv('USER_PASSWORD_1','InitUserPass!2026')
    main=importlib.import_module('app.main')
    db=create_engine(f'sqlite:///{tmp_path}/legacy.db')
    with db.begin() as conn:
        conn.execute(text('''CREATE TABLE users(email VARCHAR(255) PRIMARY KEY,
           password_hash TEXT NOT NULL,role VARCHAR(20) NOT NULL,updated_at TEXT NOT NULL)'''))
        conn.execute(text('''INSERT INTO users(email,password_hash,role,updated_at)
            VALUES ('juanjose.solanocamacho@noventiq.com','OLD_HASH','admin','old')'''))
    monkeypatch.setattr(main,'engine',db)
    monkeypatch.setattr(main,'_INITIALIZED_ENGINE',None)
    main.init_db()
    main.bootstrap_users()
    with db.connect() as conn:
        account=conn.execute(text('''SELECT email,password_hash,role,is_active,auth_version
           FROM users WHERE email='juanjose.solanocamacho@noventiq.com' ''')).one()
        assert account == ('juanjose.solanocamacho@noventiq.com','OLD_HASH','admin',1,1)
        assert conn.execute(text('SELECT COUNT(*) FROM users')).scalar_one() == 1
        assert conn.execute(text('SELECT COUNT(*) FROM app_settings')).scalar_one() == 1
    db.dispose()
