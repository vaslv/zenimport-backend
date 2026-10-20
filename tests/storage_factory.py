"""Disposable historical schemas only. Never use with application storage."""
from backend import storage


def remove_schema8(db):
    for kind,name in sorted(storage.OBJECTS-storage.OBJECTS_V7,key=lambda item:(item[0]!='trigger',item[1])):
        db.execute('DROP '+kind.upper()+' "'+name+'"')
    db.execute('DELETE FROM schema_migrations WHERE version=8')
