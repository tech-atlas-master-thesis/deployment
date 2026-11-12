// All DB_* values arrive as environment variables of the mongodb container
// (see docker-compose.yml), so no .env file has to be readable in here.
const required = [
  'DB_ROOT_USERNAME',
  'DB_ROOT_PASSWORD',
  'DB_PIPE_DATABASE',
  'DB_PIPE_USER_RW',
  'DB_PIPE_PASS_RW',
  'DB_CACHE_DATABASE',
  'DB_CACHE_USER_RW',
  'DB_CACHE_PASS_RW',
  'DB_RAW_DATABASE',
  'DB_RAW_RW_USER',
  'DB_RAW_RW_PASS',
  'DB_FE_DATABASE',
  'DB_FE_RW_USER',
  'DB_FE_RW_PASS',
  'DB_FE_R_USER',
  'DB_FE_R_PASS',
  'DB_REGISTRY_NAME',
  'DB_REGISTRY_USER',
  'DB_REGISTRY_PASS',
];
const missing = required.filter((name) => !process.env[name]);
if (missing.length) {
  throw new Error(`init-mongo.js: missing environment variables: ${missing.join(', ')}`);
}

db = db.getSiblingDB('admin');
db.auth(process.env.DB_ROOT_USERNAME, process.env.DB_ROOT_PASSWORD);

const pipe_database = process.env.DB_PIPE_DATABASE;
pipe = db.getSiblingDB(pipe_database);
pipe.createUser({
  user: process.env.DB_PIPE_USER_RW,
  pwd: process.env.DB_PIPE_PASS_RW,
  roles: [
    {
      role: 'readWrite',
      db: pipe_database,
    },
  ],
});

pipe.createCollection('schedules');
pipe.createCollection('pipelines');
pipe.createCollection('steps');

const cache_database = process.env.DB_CACHE_DATABASE;
cache = db.getSiblingDB(cache_database);
cache.createUser({
  user: process.env.DB_CACHE_USER_RW,
  pwd: process.env.DB_CACHE_PASS_RW,
  roles: [
    {
      role: 'readWrite',
      db: cache_database,
    },
  ],
});

const raw_database = process.env.DB_RAW_DATABASE;
raw = db.getSiblingDB(raw_database);
raw.createUser({
  user: process.env.DB_RAW_RW_USER,
  pwd: process.env.DB_RAW_RW_PASS,
  roles: [
    {
      role: 'readWrite',
      db: raw_database,
    },
  ],
});
raw.createCollection('csvFiles.files');
raw.createCollection('csvFiles.chunks');

raw.csvFiles.files.createIndex({ filename: 1 });
raw.csvFiles.chunks.createIndex({ files_id: 1, n: 1 }, { unique: true });

const fe_database = process.env.DB_FE_DATABASE;
fe = db.getSiblingDB(fe_database);
fe.createUser({
  user: process.env.DB_FE_RW_USER,
  pwd: process.env.DB_FE_RW_PASS,
  roles: [
    {
      role: 'readWrite',
      db: fe_database,
    },
  ],
});
fe.createUser({
  user: process.env.DB_FE_R_USER,
  pwd: process.env.DB_FE_R_PASS,
  roles: [
    {
      role: 'read',
      db: fe_database,
    },
  ],
});
fe.createCollection('projects');
fe.createCollection('organisations');
fe.createCollection('grants');

const registry_database = process.env.DB_REGISTRY_NAME;
registry = db.getSiblingDB(registry_database);
registry.createUser({
  user: process.env.DB_REGISTRY_USER,
  pwd: process.env.DB_REGISTRY_PASS,
  roles: [
    {
      role: 'readWrite',
      db: registry_database,
    },
  ],
});
