SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_NAME LIKE '%Chat%'
ORDER BY TABLE_SCHEMA, TABLE_NAME;

SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME IN ('SysChat', 'SysChat2SysResource', 'SysChat2SysWorkRoom',
                     'SysResources', 'SysLogin', 'SysWorkRoom', 'SysResource2Agent')
ORDER BY TABLE_NAME, ORDINAL_POSITION;

SELECT fk.name AS ForeignKeyName,
       OBJECT_SCHEMA_NAME(fk.parent_object_id) AS SourceSchema,
       OBJECT_NAME(fk.parent_object_id) AS SourceTable,
       COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS SourceColumn,
       OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS TargetSchema,
       OBJECT_NAME(fk.referenced_object_id) AS TargetTable,
       COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS TargetColumn
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
WHERE OBJECT_NAME(fk.parent_object_id) LIKE '%Chat%'
   OR OBJECT_NAME(fk.referenced_object_id) LIKE '%Chat%'
ORDER BY SourceTable, ForeignKeyName;

SELECT MIN(IDChat2) AS FirstIDChat2, MAX(IDChat2) AS LastIDChat2,
       COUNT_BIG(*) AS TotalMessages, MIN(Stamp) AS FirstStamp, MAX(Stamp) AS LastStamp
FROM dbo.SysChat WITH (NOLOCK);
