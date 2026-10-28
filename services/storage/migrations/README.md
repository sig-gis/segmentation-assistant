# Schema

Numbered SQL files applied by the API at startup, in name order, each in one
transaction. `001_schema.sql` creates the whole schema on an empty database.
Applied names are recorded in the `schema_migrations` table, so each file
runs once per database.

- To change the schema, add the next numbered file (`002_name.sql`).
- Never edit a file that has been applied to a database you want to keep.
