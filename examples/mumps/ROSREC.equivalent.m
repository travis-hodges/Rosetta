ROSREC ;Example only: recording a visit bumps the per-patient counter
RECORD(ID)
 N N
 S N=$G(^VISITS(ID))
 S ^VISITS(ID)=N+1
 W "RECORDED",!
 Q
