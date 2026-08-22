DATASETS = {
    "resources": """
        SELECT r.DisplayName, r.ResourceId, r.ActiveIDLogin2Resource,
               a.IDAgentResource, l.FullName
        FROM dbo.SysResources r
        INNER JOIN dbo.SysLogin l
          ON l.ActiveIDLogin2Resource = r.ActiveIDLogin2Resource
        LEFT JOIN dbo.SysResource2Agent a
          ON a.IDHumanResource = r.ResourceId AND a.Active = 1
        ORDER BY r.DisplayName ASC
    """,
    "logins": """
        SELECT Username, FullName, Password, Salt, IDLogin,
               LastIDResource, ActiveIDLogin2Resource
        FROM dbo.SysLogin
    """,
    "workrooms": """
        SELECT Code, Name, Description, IDWorkRoom
        FROM dbo.SysWorkRoom
    """,
    "workroom-resources": """
        SELECT r.DisplayName, r.ResourceId, l.FullName,
               w.Code, w.Name, w.IDWorkRoom
        FROM dbo.SysResources r
        INNER JOIN dbo.SysLogin l
          ON l.ActiveIDLogin2Resource = r.ActiveIDLogin2Resource
        INNER JOIN dbo.SysWorkRoomResource wr ON wr.IDResource = r.ResourceId
        INNER JOIN dbo.SysWorkRoom w ON w.IDWorkRoom = wr.IDWorkRoom
        ORDER BY r.DisplayName ASC
    """,
}

ACTIVE_RESOURCE_AGENT = """
    SELECT TOP 1 IDAgentResource
    FROM dbo.SysResource2Agent WITH (NOLOCK)
    WHERE IDHumanResource = %s AND Active = 1
    ORDER BY CreatedUtc DESC
"""

