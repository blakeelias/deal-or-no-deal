import os
import re
import openai
import random
import time
import pdb
import csv
from negotiation_agent import NegotiationAgent

OPENAI_API_KEY = os.environ['OPENAI_API_KEY']

class NegotiationEnvironment():
    def __init__(self, logfile, 
                 a_desc='default', b_desc='default',
                 a_prompt='CoT', b_prompt='CoT',
                 eval_model='gpt-3.5-turbo', num_turns = 3, verbose = False):
        self.model = eval_model
        items = ['book', 'hat', 'ball']

        # [num_items, alice_val, bob_val]
        # random.seed(seed)
        self.item_info = [random.choices(range(0,4), k=3) for i in range(3)]
        self.items = dict(zip(items, [i[0] for i in self.item_info]))
        self.alice_values = dict(zip(items, [i[1] for i in self.item_info]))
        self.bob_values = dict(zip(items, [i[2] for i in self.item_info]))

        self.agents = []
        self.agents.append(NegotiationAgent('Alice', 'Bob', num_turns, self.items, 
                                            self.alice_values, a_desc, a_prompt, verbose))
        self.agents.append(NegotiationAgent('Bob', 'Alice', num_turns, self.items, 
                                            self.bob_values, b_desc, b_prompt, verbose))

        self.total_turns = num_turns * len(self.agents)
        self.current_turn = 0
        self.max_attempts_per_round = 3
        self.message_history = [] # list of all messages
        self.proposal_history = [] # list of all proposals in standardized format
        self.reward_history = [] # list of rewards over time in form (A, B)
        self.logfile = logfile
        self.verbose = verbose

    def word_to_number(self, word):
        word_to_num = {
            'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'zero': 0,
        }
        return word_to_num.get(word, word)
    
    def is_accepting(self, proposal):
        proposal = proposal.lower()
        acceptance_terms = ['accepted','accept']

        # Check if any of acceptance terms above is in the proposal
        if any(term in proposal for term in acceptance_terms):
            return True
        return False

    def standardize_proposal(self, proposal_msg, next_agent):
        # Standardizing to make it easy to pick out the numbers of items an agent is proposing
        current_agent_name = next_agent.name
        if self.verbose:
            print(f'______________________{current_agent_name}______________________')
            print(f"Original generated proposal: {proposal_msg}")
        opp_agent_name = 'Bob' if current_agent_name.lower() == 'alice' else 'Alice' 
        
        # Use LLM to generate a concise version of the offer
        prompt = f"This is the full proposal message from {current_agent_name}: {proposal_msg}\nPlease tell me what items {current_agent_name} wants in a concise format, like so: '{current_agent_name}: 1 book 2 hats 3 balls'"
        response = openai.ChatCompletion.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=20  
        )
        
        generated_offer = response.choices[0].message.content.strip()
        cleaned_generated_offer = generated_offer.replace("'", "").replace("\"", "")
        
        # Directly extract item counts from the cleaned_generated_offer
        patterns = [
            (r"(\d+) book", 'book'),
            (r"(\d+) hat", 'hat'),
            (r"(\d+) ball", 'ball')
        ]
        
        items_counts = {'book': 0, 'hat': 0, 'ball': 0}
        for pattern, item in patterns:
            match = re.search(pattern, cleaned_generated_offer)
            if match:
                items_counts[item] = int(match.group(1))
        
        cleaned_generated_offer_standardized = f"{items_counts.get('book', 0)} book {items_counts.get('hat', 0)} hat {items_counts.get('ball', 0)} ball"
        if self.verbose:
            print(f"Offer from {current_agent_name}: {cleaned_generated_offer_standardized}")
        
        # Calculate opponent's offer
        opp_items_counts = {}
        for item, count in items_counts.items():
            opp_items_counts[item] = self.items[item] - count
        
        # Ensure no item has a negative count for opponent
        for item, count in opp_items_counts.items():
            if count < 0:
                opp_items_counts[item] = 0
        
        remaining_offer = f"{opp_agent_name}: {opp_items_counts.get('book', 0)} book {opp_items_counts.get('hat', 0)} hat {opp_items_counts.get('ball', 0)} ball"
        standardized_proposal = f"'{current_agent_name}: {cleaned_generated_offer_standardized} {remaining_offer}'"
        if self.verbose:
            print(f"Standardized Proposal: {standardized_proposal}")
        
        return standardized_proposal


    def check_validity(self, proposal):
        items_pattern = r"(\w+): (\w+|\d+) (book|ball|hat)s? (\w+|\d+) (book|ball|hat)s? (\w+|\d+) (book|ball|hat)s?"
        matches = re.findall(items_pattern, proposal)

        if len(matches) != 2:  # change this if we want to allow more than 2 agents
            return False

        total_counts = {}
        for match in matches:
            for i in range(1, 7, 2):
                item_count = self.word_to_number(match[i])
                item_name = match[i+1]
                total_counts[item_name] = total_counts.get(item_name, 0) + int(item_count)

        for item, count in self.items.items():
            if total_counts.get(item, 0) != count:
                return False

        return True

    def compute_rewards(self, proposal):
        # Extracting counts of each item for Alice and Bob from the proposal
        items_pattern = r"(\w+): (\w+|\d+) (book|ball|hat)s? (\w+|\d+) (book|ball|hat)s? (\w+|\d+) (book|ball|hat)s?"
        matches = re.findall(items_pattern, proposal)

        if len(matches) != 2:  
            return (0, 0)

        alice_items, bob_items = matches

        # Utility function to compute reward for an agent based on item counts and their values
        def compute_individual_reward(items, values):
            reward = 0
            for i in range(1, 7, 2):
                count = int(self.word_to_number(items[i]))
                item = items[i+1]
                reward += count * values[item]
            return reward

        alice_reward = compute_individual_reward(alice_items, self.alice_values)
        bob_reward = compute_individual_reward(bob_items, self.bob_values)

        return (alice_reward, bob_reward)
        
    def step(self):
        # plays one round in negotiation game.
        # returns True if more moves can be made.
        
        # Use modulo to switch between agents 0 and 1
        next_agent_index = self.current_turn % 2
        next_agent = self.agents[next_agent_index]

        num_attempts = 0
        turn_dict = {1:'first', 2:'second', 3:'third'}
        turn_key = self.current_turn // 2 + 1
        turn_string = turn_dict.get(turn_key, f'{turn_key}th')

        message = f'It is your turn to make the {turn_string} offer, {next_agent.name}.'
        if self.current_turn == self.total_turns - 1:
            message += ' Since this is the last turn, you must accept or have the items distributed randomly.'
        next_message = next_agent.generate(message=message)

        if self.is_accepting(next_message):
            # check if the message is an acceptance before calling the standardize proposal function
            # game is over. log outputs and rewards
            if self.proposal_history:
                self.proposal_history[-1] = "Accept"
                assert len(self.message_history) == len(self.proposal_history), "Mismatched lengths"
                to_log = [str(x) for x in [self.items, self.alice_values, self.bob_values]]
                for item1, item2, item3 in zip(self.message_history, self.proposal_history, self.reward_history):
                    to_log.extend([item1, item2, item3])

                with open(self.logfile, 'a') as f:
                    wr = csv.writer(f, quoting=csv.QUOTE_ALL)
                    wr.writerow(to_log)
                    f.write('\n')
                f.close()
                return(True)

        standardized_proposal = self.standardize_proposal(next_message, next_agent)
        # pdb.set_trace()
        while not (self.check_validity(standardized_proposal) and num_attempts < self.max_attempts_per_round):
            num_attempts += 1
            next_message = next_agent.generate()
            standardized_proposal = self.standardize_proposal(next_message, next_agent)
            time.sleep(5)

        if num_attempts > self.max_attempts_per_round:
            raise AssertionError("Too Many Attempts to Generate Valid Proposal")

        self.message_history.append(f'"{next_message}"')
        self.proposal_history.append(f'"{standardized_proposal}"')
        self.reward_history.append(str(self.compute_rewards(standardized_proposal)))

        self.current_turn += 1
        next_agent.add_message_to_history(next_message, sender='assistant')
        # Update the other agent's history as well - include original proposal
        self.agents[1 - next_agent_index].add_message_to_history(f'{next_agent.name}\'s proposal: {standardized_proposal}')

        if self.verbose:
            print(f"Current Turn: {self.current_turn}")
            print(f"Total Turns: {self.total_turns}")

        if self.current_turn >= self.total_turns or self.is_accepting(next_message):
            # game is over. log outputs and rewards
            self.proposal_history[-1] = "Accept"
            assert len(self.message_history) == len(self.proposal_history), "Mismatched lengths"
            to_log = [str(x) for x in [self.items, self.alice_values, self.bob_values]]
            for item1, item2, item3 in zip(self.message_history, self.proposal_history, self.reward_history):
                to_log.extend([item1, item2, item3])

            with open(self.logfile, 'a') as f:
                wr = csv.writer(f, quoting=csv.QUOTE_ALL)
                wr.writerow(to_log)
                f.write('\n')

            return(True)
        else:
            return(False)

        
    def reset(self):
        # resets environment while maintaining values and item counts
        num_turns = self.total_turns/len(self.agents)
        self.agents = []
        self.agents.append(NegotiationAgent('Alice', 'Bob', num_turns, self.items, self.alice_values))
        self.agents.append(NegotiationAgent('Bob', 'Alice', num_turns, self.items, self.bob_values))
        
        self.current_turn = 0
        self.message_history = [] 
        self.proposal_history = [] 
        self.reward_history = [] 

        return(None)