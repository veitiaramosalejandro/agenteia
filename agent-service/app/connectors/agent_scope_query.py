"""Fixed read-only compatibility query; keep aligned with Data API agent-scopes."""

AGENT_SCOPES_QUERY = """
SELECT DISTINCT
               r.DisplayName, r.ResourceId, lr.IDLogin, l.FullName,
               w.IDWorkRoom, w.Code AS WorkRoomCode, w.Name AS WorkRoomName,
               wr.ResourceAccessType,
               c.ID AS IDCommunity, c.Name AS CommunityName,
               c.Description AS CommunityDescription, c.Code AS CommunityCode,
               e.organizationid, e.organization_no, e.accountname
        FROM dbo.SysResources r
        INNER JOIN dbo.SysResource2Agent ra
          ON ra.IDHumanResource = r.ResourceId AND ra.Active = 1
        INNER JOIN dbo.SysWorkRoomResource wr ON wr.IDResource = r.ResourceId
        INNER JOIN dbo.SysWorkRoom w ON w.IDWorkRoom = wr.IDWorkRoom
        INNER JOIN dbo.SysCommunity2WorkRoom cw ON cw.IDWorkRoom = w.IDWorkRoom
        INNER JOIN dbo.SysCommunity c ON c.ID = cw.IDCommunity
        INNER JOIN dbo.SysCommunity2Resource cr
          ON cr.IDCommunity = c.ID AND cr.IDResource = r.ResourceId
        INNER JOIN dbo.SysLogin2SysResource lr ON lr.IDResource = r.ResourceId
        INNER JOIN dbo.SysLogin l ON l.IDLogin = lr.IDLogin
        INNER JOIN dbo.SysCommunity2Company cc ON cc.IDCommunity = c.ID
        INNER JOIN dbo.Entity e ON e.ID = cc.IDCompany
"""
