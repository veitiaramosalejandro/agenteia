DECLARE @LastIDChat2 bigint = 0;
DECLARE @BatchSize int = 500;

SELECT TOP (@BatchSize)
    c.IDChat2, c.IDSender, sender.IDResource AS IDSenderResource,
    c.RawMessage, c.Stamp, c.Kind, c.Importance, c.Status,
    cw.IDWorkRoom, w.Code AS WorkRoomCode, w.Name AS WorkRoomName,
    l.FullName, r.DisplayName AS ResourceName,
    CASE WHEN agent.IDAgentResource IS NULL THEN 0 ELSE 1 END AS GeneratedByIA
FROM dbo.SysChat c WITH (NOLOCK)
OUTER APPLY (
    SELECT TOP (1) rel.IDResource
    FROM dbo.SysChat2SysResource rel WITH (NOLOCK)
    WHERE rel.IDChat = c.IDChat2 AND rel.IDLogin = c.IDSender
    ORDER BY rel.IDResource
) sender
OUTER APPLY (
    SELECT TOP (1) rel.IDWorkRoom
    FROM dbo.SysChat2SysWorkRoom rel WITH (NOLOCK)
    WHERE rel.IDChat2 = c.IDChat2
    ORDER BY rel.IDWorkRoom
) cw
LEFT JOIN dbo.SysWorkRoom w WITH (NOLOCK) ON w.IDWorkRoom = cw.IDWorkRoom
LEFT JOIN dbo.SysResources r WITH (NOLOCK) ON r.ResourceId = sender.IDResource
LEFT JOIN dbo.SysLogin l WITH (NOLOCK) ON l.IDLogin = c.IDSender
LEFT JOIN dbo.SysResource2Agent agent WITH (NOLOCK)
    ON agent.IDAgentResource = sender.IDResource AND agent.Active = 1
WHERE c.IDChat2 > @LastIDChat2
  AND c.RawMessage IS NOT NULL
  AND LTRIM(RTRIM(c.RawMessage)) <> ''
ORDER BY c.IDChat2 ASC;
