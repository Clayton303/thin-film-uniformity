"""
MariaDB export for normalized uniformity curve data.

Expected schema (run once on the dashboard database):

    CREATE TABLE uniformity_runs (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        chamber      VARCHAR(10)  NOT NULL,
        design_name  VARCHAR(100) NOT NULL,
        run_date     DATE         NOT NULL,
        f_factor     FLOAT,
        created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_run (chamber, run_date)
    );

    CREATE TABLE uniformity_curve (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        run_id         INT     NOT NULL,
        radius         FLOAT   NOT NULL,
        ta2o5_scale    FLOAT   NOT NULL,
        sio2_scale     FLOAT   NOT NULL,
        ta2o5_norm     FLOAT   NOT NULL,
        sio2_norm      FLOAT   NOT NULL,
        merit          FLOAT,
        converged      TINYINT(1),
        FOREIGN KEY (run_id) REFERENCES uniformity_runs(id) ON DELETE CASCADE,
        UNIQUE KEY uq_point (run_id, radius)
    );
"""

import pymysql
import pymysql.cursors
from datetime import date


def connect(host: str, user: str, password: str, database: str, port: int = 3306):
    """Return an open PyMySQL connection to the dashboard MariaDB."""
    return pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        port=port,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def ensure_schema(conn) -> None:
    """Create tables if they don't exist. Safe to call on every run."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uniformity_runs (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                chamber      VARCHAR(10)  NOT NULL,
                design_name  VARCHAR(100) NOT NULL,
                run_date     DATE         NOT NULL,
                f_factor     FLOAT,
                created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_run (chamber, run_date)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uniformity_curve (
                id             INT AUTO_INCREMENT PRIMARY KEY,
                run_id         INT     NOT NULL,
                radius         FLOAT   NOT NULL,
                ta2o5_scale    FLOAT   NOT NULL,
                sio2_scale     FLOAT   NOT NULL,
                ta2o5_norm     FLOAT   NOT NULL,
                sio2_norm      FLOAT   NOT NULL,
                merit          FLOAT,
                converged      TINYINT(1),
                FOREIGN KEY (run_id) REFERENCES uniformity_runs(id) ON DELETE CASCADE,
                UNIQUE KEY uq_point (run_id, radius)
            )
        """)
    conn.commit()


def export_run(
    conn,
    chamber: str,
    design_name: str,
    run_date: date,
    f_factor: float,
    curve_points: list[dict],
) -> int:
    """
    Upsert one uniformity run and its normalized curve into the database.

    curve_points: list of dicts with keys:
        radius, ta2o5_scale, sio2_scale, ta2o5_norm, sio2_norm, merit, converged

    Returns the run_id.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO uniformity_runs (chamber, design_name, run_date, f_factor)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                design_name = VALUES(design_name),
                f_factor    = VALUES(f_factor),
                id          = LAST_INSERT_ID(id)
        """, (chamber, design_name, run_date, f_factor))

        run_id = cur.lastrowid

        for pt in curve_points:
            cur.execute("""
                INSERT INTO uniformity_curve
                    (run_id, radius, ta2o5_scale, sio2_scale, ta2o5_norm, sio2_norm, merit, converged)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ta2o5_scale = VALUES(ta2o5_scale),
                    sio2_scale  = VALUES(sio2_scale),
                    ta2o5_norm  = VALUES(ta2o5_norm),
                    sio2_norm   = VALUES(sio2_norm),
                    merit       = VALUES(merit),
                    converged   = VALUES(converged)
            """, (
                run_id,
                pt["radius"],
                pt["ta2o5_scale"],
                pt["sio2_scale"],
                pt["ta2o5_norm"],
                pt["sio2_norm"],
                pt.get("merit"),
                int(pt.get("converged", False)),
            ))

    conn.commit()
    return run_id
