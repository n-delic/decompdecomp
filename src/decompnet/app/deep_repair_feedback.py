"""Share output rotation and earned sequence limits with the one-function deep search.

Only fresh compiler output fingerprints teach feedback. Missing imported/cache evidence stays unknown.
"""
from . import repair_effects, repair_sequence_lane


class Feedback:
    def __init__(self, source, score, rows, output=None, granularity="function"):
        self.state=dict(pending=[],seen=[],counts={})
        self.proofs={source:dict(source=source,score=score,depth=0,path=[],productive_steps=0,
                                report={'rows':rows},output=output,granularity=granularity)}
        self.loan_sources=set()

    def record(self, parent, candidate, family, path, score, rows, output, reused=False, granularity="function"):
        before=self.proofs.get(parent,{})
        node=dict(source=candidate,parent_source=parent,parent_output=before.get('output'),path=path+[family])
        changed=output is not None and before.get('output') is not None and output!=before['output']
        productive=changed and isinstance(score,(int,float)) and score<before.get('score',score)
        if not reused:
            repair_effects.observe(self.state,node,output if score is not None else None,score=score,parent_score=before.get('score'))
            counts=self.state['counts'];kind='scored' if score is not None else 'compile_failed'
            counts[kind]=counts.get(kind,0)+1
        if score is not None:
            self.proofs[candidate]={**node,'score':score,'ceiling':before.get('score',score),'depth':len(path)+1,
                'baseline_rows':before.get('report',{}).get('rows'),'report':{'rows':rows},'output':output,'granularity':granularity,
                'productive_steps':before.get('productive_steps',0)+int(productive),
                'unchanged_steps':int(output is not None and output==before.get('output'))}

    def order(self, queue, labels, source, parents):
        self.state['pending']=[dict(source=c,parent_source=parents.get(c,(source,))[0],
            parent_output=self.proofs.get(parents.get(c,(source,))[0],{}).get('output'),
            path=self.proofs.get(parents.get(c,(source,))[0],{}).get('path',[])+[labels.get(c,'local_repair')]) for c in queue]
        repair_effects.rotate(self.state)
        queue[:]=[n['source'] for n in self.state['pending']]

    def eligible(self, source, depth, arch):
        proof=self.proofs.get(source,{})
        return proof.get('granularity','function')=='function' and repair_sequence_lane.eligible(proof,depth,arch)

    def continuation(self, source, choices, labels, seen):
        self.state['seen']=seen
        selected=repair_sequence_lane.select(self.state,self.proofs[source],[(labels[c],c) for c in choices])
        self.loan_sources.update(c for _,c in selected)
        return [c for _,c in selected]

    def summary(self):
        return repair_effects.summary(self.state)

    def pairs(self, parent, source, family, score, output, header='', path=()):
        from .inert_pair_search import offer
        before = self.proofs.get(parent,{})
        if before.get('granularity','function')!='function':return []
        return offer(self.state,parent,source,family,score,before.get('score'),before.get('output'),output,header=header,path=path)
