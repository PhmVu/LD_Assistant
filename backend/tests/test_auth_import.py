import sys
sys.stdout.reconfigure(encoding='utf-8')
try:
    import routes.auth as auth
    print("Import OK")
    print(dir(auth))
except Exception as e:
    import traceback
    traceback.print_exc()
