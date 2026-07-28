from database import initialize_database,add_alert
def main():
    initialize_database()
    add_alert("system","info","Repository installed","GARIBALDI MARKET ORACLE is ready. Start worker.py to begin scanning.",source="system")
    print("Database initialized.")
if __name__=="__main__": main()
