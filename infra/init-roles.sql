-- Rôles de CONNEXION (LOGIN) créés à l'init du conteneur postgres.
-- Les GRANT et policies sont posés par la migration 0002_rls.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='app_api') THEN
    CREATE ROLE app_api LOGIN PASSWORD 'app_api';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='app_worker') THEN
    CREATE ROLE app_worker LOGIN PASSWORD 'app_worker' BYPASSRLS;
  END IF;
END $$;
