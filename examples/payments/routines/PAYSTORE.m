PAYSTORE ; Persistent payment ledger storage
BALANCE(ACCOUNT) ; account balance in cents
 Q +$G(^RPAY("balance",ACCOUNT))
SAVE(ACCOUNT,AMOUNT) ; persist balance
 S ^RPAY("balance",ACCOUNT)=AMOUNT
 Q
RECEIPT(ID,RECORD) ; save the payment receipt
 S ^RPAY("receipt",ID)=RECORD
 Q
FIND(ID) ; receipt string or empty when absent
 Q $G(^RPAY("receipt",ID))
