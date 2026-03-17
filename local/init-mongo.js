const { loadEnvFile } = require('node:process');
loadEnvFile();

db = db.getSiblingDB('admin');
db.auth(process.env.DB_ROOT_USERNAME, process.env.DB_ROOT_PASSWORD);

const pipe_database = process.env.DB_PIPE_DATABASE;
pipe = db.getSiblingDB(pipe_database);
pipe.createUser({
  user: process.env.DB_PIPE_USER_RW,
  pwd: process.env.DB_PIPE_USER_RW,
  roles: [
    {
      role: 'readWrite',
      db: pipe_database,
    },
  ],
});

pipe.createCollection('scraper');
pipe.createCollection('transformer');

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
fe.createCollection('grants')
