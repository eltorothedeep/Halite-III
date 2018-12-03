#!/usr/bin/env python3

# Import the Halite SDK, which will let you interact with the game.
import hlt
from hlt import constants

import random
import logging
from enum import IntEnum, auto

# v1 base bot 
# v2 better movement
# v3 goal based movement
# v4 stuck resolution
# v5 Fib based ship cost, no limit, no delay, return when closer to full
# v6 Homing at end, start with DEPOSITING, EXPLORE range increase over game after DEPOSITING, 
#    +1 range on EXPLORING, fix no move bug, strip mine

class shipInfo(IntEnum):
    STATE = 0
    GOAL = auto()
    LASTPOS = auto()
#

class shipState(IntEnum):
    EXPLORING = auto()
    RETURNING = auto()
    DEPOSITING = auto()
    HOMING = auto()
    CONVERTING = auto()
#

useSaboteurs = False
ship_status = {}

def fibbing(n):
    if n == 0:
        return 0
    elif n==1 or n==2:
        return 1
        
    return fibbing(n-1)+fibbing(n-2)
#

def GetFib(fn, arg):
    fibs = {}
    if arg not in fibs:
        fibs[arg] = fn(arg)
    return fibs[arg]
#

def GetShipBuildThreshold(num):
    return int(constants.SHIP_COST * (1+(GetFib(fibbing, num)/100)))
#    

def GetRichestPosition( curPos, range, game_map ):
    first = True
    range *= 4
    best = curPos
    max = game_map[curPos].halite_amount
    adList = curPos.get_surrounding_cardinals()
    for adjacent in adList:
        if range > 0:
            for ad1 in adjacent.get_surrounding_cardinals():
                adList.append(ad1)
            #
            range -= 1
        #
        
        if game_map[adjacent].is_empty and ((first and game_map[adjacent].halite_amount >= max) or (game_map[adjacent].halite_amount > max)):
            best = adjacent
            max = game_map[adjacent].halite_amount
        #
        first = False
    #
    return best
#

def GetClosestStoragePosition(position, me):
    return me.shipyard.position
#

# This game object contains the initial game state.
game = hlt.Game()
# Respond with your name.
game.ready("DeepCv6")

while True:
    # Get the latest game state.
    game.update_frame()
    # You extract player metadata and the updated map metadata here for convenience.
    me = game.me
    game_map = game.game_map

    # A command queue holds all the commands you will run this turn.
    command_queue = []

    numships = len(me.get_ships())
    exploring = 0
    returning = 0
    for ship in me.get_ships():
        if ship.id not in ship_status:
            ship_status[ship.id] = [shipState.DEPOSITING, None, ship.position]
            #exploring += 1
        elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            if ship.position == ship_status[ship.id][shipInfo.LASTPOS]:
                ship_status[ship.id][shipInfo.STATE] = shipState.EXPLORING
                ship_status[ship.id][shipInfo.GOAL] = None
                exploring += 1
            else:
                returning += 1
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
            exploring += 1
            if ship.position == ship_status[ship.id][shipInfo.LASTPOS]:
                ship_status[ship.id][shipInfo.GOAL] = None
            #
        #
        if ship_status[ship.id][shipInfo.STATE] != shipState.HOMING:
            turns_left = constants.MAX_TURNS - game.turn_number
            if turns_left < 100:
                storage = GetClosestStoragePosition(ship.position, me)
                distance = game_map.calculate_distance(ship.position, storage)
                if (distance * 2) > turns_left:
                    if ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
                        exploring -= 1
                    elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
                        returning -= 1
                    #
                    ship_status[ship.id][shipInfo.STATE] = shipState.HOMING
                    ship_status[ship.id][shipInfo.GOAL] = storage
                    #logging.info("Ship {} HOMING Distance {}".format(ship.id, distance))
                #
            #
        #
        #logging.info("Ship {} state {} goal {} pos {} halite {}.".format(ship.id, str(ship_status[ship.id][shipInfo.STATE]), ship_status[ship.id][shipInfo.GOAL], ship.position, ship.halite_amount))
        ship_status[ship.id][shipInfo.LASTPOS] = ship.position
    #
    #logging.info("PrePro Complete")

    for ship in me.get_ships():        
        if ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            if ship.position == ship_status[ship.id][shipInfo.GOAL]:
                ship_status[ship.id][shipInfo.STATE] = shipState.DEPOSITING
            else:
                move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(move))
                continue
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.HOMING:
            if game_map.calculate_distance(ship.position, ship_status[ship.id][shipInfo.GOAL]) == 1:
                moves = game_map.get_unsafe_moves(ship.position, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(moves[0]))
            else:
                move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(move))
            #
            continue
        elif (ship.halite_amount > int(constants.MAX_HALITE * 0.5) and returning == 0) or ship.is_full:
            ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
            ship_status[ship.id][shipInfo.GOAL] = GetClosestStoragePosition(ship.position, me)
            move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
            command_queue.append(ship.move(move))
            continue
        #
        
        # For each of your ships, move  if the ship is on a low halite location 
        #   Else, collect halite.
        if ship_status[ship.id][shipInfo.STATE] == shipState.DEPOSITING: 
            ship_status[ship.id][shipInfo.STATE] = shipState.EXPLORING
            best = GetRichestPosition( ship.position, int(game.turn_number/100), game_map )
            if game_map.calculate_distance(ship.position, best) > 1:
                ship_status[ship.id][shipInfo.GOAL] = best
            #
            move = game_map.naive_navigate(ship, best)
            #logging.info("Ship {} Goal {} Move {}".format(ship.id, best, move))
            command_queue.append(ship.move(move))
            break
        elif ship_status[ship.id][shipInfo.GOAL] is not None:
            if ship_status[ship.id][shipInfo.GOAL] == ship.position:
                ship_status[ship.id][shipInfo.GOAL] = None
                command_queue.append(ship.stay_still())
            else:
                move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(move))            
            #
        elif game_map[ship.position].halite_amount < constants.MAX_HALITE / 15:
            #best = GetRichestPosition( ship.position, int((constants.MAX_TURNS-game.turn_number)/100), game_map )
            best = GetRichestPosition( ship.position, 1, game_map )
            if game_map.calculate_distance(ship.position, best) > 1:
                ship_status[ship.id][shipInfo.GOAL] = best
            #
            move = game_map.naive_navigate(ship, best)
            command_queue.append(ship.move(move))
        else:
            command_queue.append(ship.stay_still())
        #
    #
        
    # If you're on the first turn and have enough halite, spawn a ship.
    # Don't spawn a ship if you currently have a ship at port, though.
    if game.turn_number <= 1 or (me.halite_amount >= GetShipBuildThreshold(numships) and game.turn_number < int(constants.MAX_TURNS*0.8) and not game_map[me.shipyard].is_occupied):
        command_queue.append(game.me.shipyard.spawn())
    #
    
    # Send your moves back to the game environment, ending this turn.
    game.end_turn(command_queue)
#