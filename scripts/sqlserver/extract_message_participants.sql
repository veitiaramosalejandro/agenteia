DECLARE @FirstIDChat2 bigint = 0;
DECLARE @LastIDChat2 bigint = 0;

SELECT rel.IDChat AS IDChat2, rel.IDLogin, rel.IDResource,
       room.IDWorkRoom,
       CASE WHEN rel.IDLogin = chat.IDSender THEN 1 ELSE 2 END AS Type
FROM dbo.SysChat2SysResource rel WITH (NOLOCK)
JOIN dbo.SysChat chat WITH (NOLOCK) ON chat.IDChat2 = rel.IDChat
LEFT JOIN dbo.SysChat2SysWorkRoom room WITH (NOLOCK) ON room.IDChat2 = chat.IDChat2
WHERE chat.IDChat2 BETWEEN @FirstIDChat2 AND @LastIDChat2
ORDER BY chat.IDChat2, Type, rel.IDResource;
