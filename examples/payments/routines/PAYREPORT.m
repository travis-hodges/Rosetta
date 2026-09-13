PAYREPORT ; Query service
ACCOUNT(ACCOUNT) ; existing reporting behavior
 Q ACCOUNT_"^"_$$BALANCE^PAYSTORE(ACCOUNT)
TOTAL() ; sum balances in deterministic collation order
 N ACCOUNT,TOTAL
 S ACCOUNT="",TOTAL=0
 F  S ACCOUNT=$O(^RPAY("balance",ACCOUNT)) Q:ACCOUNT=""  S TOTAL=TOTAL+^RPAY("balance",ACCOUNT)
 Q TOTAL
