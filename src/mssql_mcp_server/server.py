import asyncio
import logging
import os
from pyodbc import connect, Error
from mcp.server import Server
from mcp.types import Resource, Tool, TextContent
from pydantic import AnyUrl

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mssql_mcp_server")

def get_db_config():
    """Get database configuration from environment variables."""
    logger.info("Reading database configuration from environment variables...")
    
    # Helper function to clean environment variables (remove surrounding quotes)
    def clean_env_var(env_var, default=None):
        value = os.getenv(env_var, default)
        if value and isinstance(value, str):
            # Remove surrounding single or double quotes if present
            if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]
        return value
    
    driver = clean_env_var("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    server = clean_env_var("MSSQL_HOST", "localhost")
    port = clean_env_var("MSSQL_PORT", "1433")
    user = clean_env_var("MSSQL_USER")
    password = clean_env_var("MSSQL_PASSWORD")
    database = clean_env_var("MSSQL_DATABASE")
    
    # 비밀번호가 올바르게 전달되었는지 확인하기 위한 디버깅 로그 (일부만 표시)
    if password and len(password) > 4:
        # 비밀번호의 일부만 출력 (보안상 전체 표시는 안 함)
        password_hint = f"{password[:2]}...{password[-2:]}"
        special_chars = ''.join([c for c in password if not c.isalnum()])
        logger.info(f"Debug - Password format: begins with '{password[:2]}', ends with '{password[-2:]}', contains special chars: '{special_chars}'")
    else:
        logger.warning("Password is too short or not provided!")
    
    # 자세한 로깅을 추가하여 실제로 어떤 값들이 설정되었는지 볼 수 있습니다
    logger.info(f"Using driver: {driver}")
    logger.info(f"Connecting to server: {server}:{port}")
    logger.info(f"Database: {database}, User: {user}")
    
    if not all([user, password, database]):
        logger.error("Missing required database configuration. Please check environment variables:")
        logger.error("MSSQL_USER, MSSQL_PASSWORD, and MSSQL_DATABASE are required")
        raise ValueError("Missing required database configuration")

    # MSSQL에 맞는 ODBC 연결 문자열 설정
    # DSN 대신 Driver={}; 형식으로 직접 설정
    connection_params = []
    
    # 기본 연결 정보
    connection_params.append(f"Driver={{{driver}}}")
    connection_params.append(f"Server={server},{port}")
    connection_params.append(f"Database={database}")
    connection_params.append(f"UID={user}")
    connection_params.append(f"PWD={password}")
    
    # 고급 옵션 (기본값 제공)
    connection_params.append(f"TrustServerCertificate={os.getenv('TrustServerCertificate', 'yes')}")
    connection_params.append(f"Encrypt={os.getenv('Encrypt', 'yes')}")
    connection_params.append(f"Connection Timeout={os.getenv('ConnectionTimeout', '60')}")
    connection_params.append(f"Login Timeout={os.getenv('LoginTimeout', '60')}")
    
    # 특별한 MS SQL Server ODBC 관련 설정
    # MARS(Multiple Active Result Sets) 활성화
    connection_params.append("MARS_Connection=yes")
    
    # 연결 문자열 생성
    connection_string = ";".join(connection_params)
    
    logger.info(f"Connection string format: Driver={{...}};Server=...;Database=...;UID=...;PWD=***;...")
    
    config = {
        "driver": driver,
        "server": f"{server}:{port}",
        "user": user,
        "database": database,
        "password": "********"  # 로그에 비밀번호 노출 방지
    }
    
    return config, connection_string

# Initialize server
app = Server("mssql_mcp_server")

@app.list_resources()
async def list_resources() -> list[Resource]:
    """List MSSQL tables as resources."""
    config, connection_string = get_db_config()
    try:
        logger.info("Attempting to connect to database to list resources...")
        with connect(connection_string) as conn:
            logger.info("Database connection established successfully")
            with conn.cursor() as cursor:
                # SQL Server INFORMATION_SCHEMA 쿼리를 사용하여 테이블 목록 조회
                cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE';")
                tables = cursor.fetchall()
                logger.info(f"Found {len(tables)} tables")
                
                resources = []
                for table in tables:
                    resources.append(
                        Resource(
                            uri=f"mssql://{table[0]}/data",
                            name=f"Table: {table[0]}",
                            mimeType="text/plain",
                            description=f"Data in table: {table[0]}"
                        )
                    )
                return resources
    except Error as e:
        error_msg = str(e)
        logger.error(f"Failed to list resources: {error_msg}")
        # 오류 메시지에 따라 구체적인 대응책 기록
        if "Login timeout expired" in error_msg:
            logger.error("Connection timed out - check network/firewall settings or increase timeout")
        elif "Cannot open database" in error_msg:
            logger.error("Database access error - check database name and user permissions")
        elif "Login failed" in error_msg:
            logger.error("Authentication failed - check username and password")
        return []

@app.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    """Read table contents."""
    config, connection_string = get_db_config()
    uri_str = str(uri)
    logger.info(f"Reading resource: {uri_str}")
    
    if not uri_str.startswith("mssql://"):
        raise ValueError(f"Invalid URI scheme: {uri_str}")
        
    parts = uri_str[8:].split('/')
    table = parts[0]
    
    try:
        with connect(connection_string) as conn:
            with conn.cursor() as cursor:
                # SQL Server는 LIMIT 대신 TOP을 사용합니다
                cursor.execute(f"SELECT TOP 100 * FROM {table}")
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                result = [",".join(map(str, row)) for row in rows]
                return "\n".join([",".join(columns)] + result)
                
    except Error as e:
        logger.error(f"Database error reading resource {uri}: {str(e)}")
        raise RuntimeError(f"Database error: {str(e)}")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available MSSQL tools."""
    logger.info("Listing tools...")
    return [
        Tool(
            name="execute_sql",
            description="Execute an SQL query on the MSSQL server",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SQL query to execute"
                    }
                },
                "required": ["query"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute SQL commands."""
    config, connection_string = get_db_config()
    logger.info(f"Calling tool: {name} with arguments: {arguments}")
    
    if name != "execute_sql":
        raise ValueError(f"Unknown tool: {name}")
    
    query = arguments.get("query")
    if not query:
        raise ValueError("Query is required")
    
    try:
        with connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                
                # Special handling for listing tables in MSSQL
                if query.strip().upper() == "SHOW TABLES":
                    cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE';")
                    tables = cursor.fetchall()
                    result = [f"Tables_in_{config['database']}"]  # Header
                    result.extend([table[0] for table in tables])
                    return [TextContent(type="text", text="\n".join(result))]
                
                # Regular SELECT queries
                elif query.strip().upper().startswith("SELECT"):
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    result = [",".join(map(str, row)) for row in rows]
                    return [TextContent(type="text", text="\n".join([",".join(columns)] + result))]
                
                # Non-SELECT queries
                else:
                    conn.commit()
                    return [TextContent(type="text", text=f"Query executed successfully. Rows affected: {cursor.rowcount}")]
                
    except Exception as e:
        logger.error(f"Error executing SQL '{query}': {e}")
        return [TextContent(type="text", text=f"Error executing query: {str(e)}")]

async def main():
    """Main entry point to run the MCP server."""
    from mcp.server.stdio import stdio_server
    
    logger.info("Starting MSSQL MCP server...")
    config, _ = get_db_config()
    logger.info(f"Database config: {config['server']}/{config['database']} as {config['user']}")
    
    async with stdio_server() as (read_stream, write_stream):
        try:
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
        except Exception as e:
            logger.error(f"Server error: {str(e)}", exc_info=True)
            raise

if __name__ == "__main__":
    asyncio.run(main())
